"""Pure-Python multi-turn runner shared by application Agent hosts.

This module intentionally knows nothing about Qt, sockets, database drivers,
or filesystem access. A model adapter and a closed tool registry are injected
by the selected host. The runner owns only orchestration:
messages are paired by ``tool_call_id``, a completed call is idempotent within
one run, and cancellation closes the run so callbacks/results that arrive
later cannot mutate it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
import threading
from typing import Any, Callable

from .budget import Budget, BudgetExceeded, BudgetLimits
from .context import ContextPlanner
from .contracts import (
    AgentEvent,
    AgentEventEmitter,
    AgentEventType,
    AgentRunResult,
    AgentStatus,
    AgentTurn,
    AgentTurnRequest,
    CancellationToken,
    ModelDelta,
    ModelAdapter,
    RunContext,
    ToolCall,
    ToolRegistry,
    ToolResult,
    stable_json,
)
from .request_budget import RequestInputLimitExceeded


# Identical consecutive read-only calls: remind on the 3rd, stop on the 5th.
_REPEAT_REMIND_AFTER = 2
_REPEAT_STOP_AFTER = 4
_REPEAT_REMINDER = (
    "This exact call was already made and its result is in the previous tool message. "
    "Do not repeat it; use that result and take the next step (for example read a specific file). "
    "相同调用已执行过，结果见上一条工具消息；请直接基于已有结果继续下一步。"
)

_REASONING_KINDS = frozenset(
    {
        "reasoning",
        "reasoning_delta",
        "model_reasoning_delta",
        "reasoning_content",
        "analysis",
        "analysis_delta",
        "thought",
        "thought_delta",
    }
)
_TEXT_KINDS = frozenset(
    {
        "text",
        "text_delta",
        "model_text_delta",
        "content",
        "content_delta",
        "output",
        "output_delta",
    }
)
_TOOL_DELTA_KINDS = frozenset(
    {
        "tool_call",
        "tool_call_delta",
        "function_call",
        "function_call_delta",
    }
)
_USAGE_KINDS = frozenset({"usage", "done", "turn_done", "complete", "completed"})
_CLARIFY_REASONS = frozenset({"clarify", "clarification", "needs_clarification"})
_LENGTH_REASONS = frozenset({"length", "max_tokens", "budget"})
def _as_turn(value: Any) -> AgentTurn:
    """Normalize a test double/adapter value without parsing free-form text."""

    if isinstance(value, AgentTurn):
        return value
    if isinstance(value, Mapping):
        return AgentTurn(
            text=str(value.get("text", value.get("content", "")) or ""),
            tool_calls=value.get("tool_calls", value.get("toolCalls", ())) or (),
            finish_reason=str(value.get("finish_reason", value.get("finishReason", "stop")) or "stop"),
            usage=value.get("usage", {}) or {},
            model_id=str(value.get("model_id", value.get("model", "")) or ""),
        )
    raise TypeError("model adapter returned an unsupported turn")


def _definition_name(item: Any) -> str:
    if not isinstance(item, Mapping):
        return ""
    name = item.get("name")
    if name:
        return str(name)
    function = item.get("function")
    if isinstance(function, Mapping) and function.get("name"):
        return str(function["name"])
    return ""


def _estimate_tokens(text: str) -> int:
    """A conservative fallback when a model adapter omits usage metadata."""

    if not text:
        return 0
    # Four bytes is a useful lower bound for common UTF-8 model output while
    # still keeping this fallback deterministic for fake adapters.
    return max(1, math.ceil(len(text.encode("utf-8", errors="replace")) / 4))


def _safe_error(prefix: str, error: BaseException | None = None) -> str:
    """Return a non-secret protocol error.

    Exception text can contain SQL literals, connection addresses, or
    credentials.  The Agent result and event journal therefore carry only a
    stable code; the original exception is intentionally not serialized.
    """

    if error is None:
        return prefix
    return f"{prefix}:{type(error).__name__}"


class AgentRunner:
    """Drive one read-only Agent run using injected model and tool objects.

    ``run`` is synchronous by design so a host can place it in its own worker
    thread.  The injected adapter is responsible for real streaming and for
    honoring the cancellation token.  ``on_event`` is called for every event
    while the run is open; callback failures are isolated from the runner.
    """

    def __init__(
        self,
        model_adapter: ModelAdapter | None = None,
        tool_registry: ToolRegistry | None = None,
        *,
        model: ModelAdapter | None = None,
        tools: ToolRegistry | None = None,
        system_prompt: str = "",
        budget: Budget | None = None,
        budget_limits: BudgetLimits | Mapping[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
        event_sink: Callable[[AgentEvent], None] | None = None,
        is_query_tool: Callable[[ToolCall], bool] | None = None,
        is_repeatable_tool: Callable[[ToolCall], bool] | None = None,
        authorize_tool_batch: Callable[[Sequence[ToolCall], RunContext, CancellationToken], bool] | None = None,
        context_planner: ContextPlanner | None = None,
    ) -> None:
        self.model_adapter = model_adapter or model
        self.tool_registry = tool_registry or tools
        self.system_prompt = str(system_prompt)
        self.default_budget = budget
        self.budget_limits = budget_limits
        self.cancellation = cancellation or CancellationToken()
        self.on_event = on_event or event_sink
        self.is_query_tool = is_query_tool or (lambda _call: False)
        self.is_repeatable_tool = is_repeatable_tool or (lambda _call: False)
        self.authorize_tool_batch = authorize_tool_batch
        self.context_planner = context_planner

    def cancel(self, reason: str = "cancelled") -> None:
        """Request one-way cancellation of the currently associated run."""

        self.cancellation.cancel(reason)

    def _new_budget(self, override: Budget | None) -> Budget:
        if override is not None:
            return override
        if self.default_budget is not None:
            return self.default_budget
        return Budget(self.budget_limits)

    def _tool_definitions(self) -> tuple[tuple[Mapping[str, Any], ...], set[str], bool]:
        registry = self.tool_registry
        if registry is None:
            raise RuntimeError("tool registry is not configured")
        method = getattr(registry, "definitions", None)
        if not callable(method):
            # Keep the injected protocol permissive for tiny test doubles, but
            # do not claim a closed set if no declaration was supplied.
            return (), set(), False
        raw = method()
        if raw is None:
            return (), set(), True
        if isinstance(raw, Mapping):
            raw_items = tuple(raw.values())
        else:
            raw_items = tuple(raw)
        definitions = tuple(item for item in raw_items if isinstance(item, Mapping))
        names = {_definition_name(item) for item in definitions}
        names.discard("")
        return definitions, names, True

    def run(
        self,
        context: RunContext | str,
        user_message: str | RunContext | None = None,
        *,
        messages: Sequence[Mapping[str, Any]] | None = None,
        initial_messages: Sequence[Mapping[str, Any]] | None = None,
        system_prompt: str | None = None,
        cancellation: CancellationToken | None = None,
        budget: Budget | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AgentRunResult:
        """Run until a final answer, clarification, stop, failure, or limit.

        ``run(context, question)`` is the canonical form.  The reversed
        ``run(question, context)`` form is accepted for small host adapters so
        a contract migration does not create an accidental second runner.
        """

        def looks_like_context(value: Any) -> bool:
            return all(hasattr(value, name) for name in ("run_id", "tab_id", "model_config_id", "as_dict"))

        if isinstance(context, str) and looks_like_context(user_message):
            context, user_message = user_message, context
        if not looks_like_context(context):
            raise TypeError("context must be a RunContext")
        if user_message is not None and not isinstance(user_message, str):
            raise TypeError("user_message must be a string")
        if self.model_adapter is None:
            raise RuntimeError("model adapter is not configured")

        # Optional host capability.  Capture it once at the start of this
        # logical run so a stale batch can never be redirected to a newer
        # registry epoch.  Generic registries without this capability keep
        # their existing behavior.
        commit_tool_batch = None
        try:
            begin_tool_commit = getattr(self.tool_registry, "begin_tool_commit", None)
            if callable(begin_tool_commit):
                candidate = begin_tool_commit(context)
                if callable(candidate):
                    commit_tool_batch = candidate
        except Exception:
            disable = getattr(self.tool_registry, "disable_evidence", None)
            if callable(disable):
                try:
                    disable(context)
                except Exception:
                    pass

        def disable_tool_evidence() -> None:
            nonlocal commit_tool_batch
            capability = commit_tool_batch
            commit_tool_batch = None
            disable_bound = getattr(capability, "disable_evidence", None)
            if callable(disable_bound):
                try:
                    disable_bound()
                    return
                except Exception:
                    pass
            disable = getattr(self.tool_registry, "disable_evidence", None)
            if callable(disable):
                try:
                    disable(context)
                except Exception:
                    pass

        token = cancellation or self.cancellation
        self.cancellation = token
        run_budget = self._new_budget(budget)
        source_messages = messages if messages is not None else initial_messages
        private_history = any("reasoning_content" in item for item in (source_messages or ()))
        # Private continuation belongs only to an adapter. Reject it at the
        # public boundary and exclude it even from the terminal transcript.
        working_messages: list[dict[str, Any]] = deepcopy([
            {key: value for key, value in item.items() if key != "reasoning_content"}
            for item in (source_messages or ())
        ])
        question = user_message or ""
        if question and not (
            working_messages
            and working_messages[-1].get("role") == "user"
            and working_messages[-1].get("content") == question
        ):
            working_messages.append({"role": "user", "content": question})

        emitted: list[AgentEvent] = []
        tool_results: list[ToolResult] = []
        verified_query_ids: list[str] = []
        call_cache: dict[str, tuple[tuple[str, str], ToolResult]] = {}
        recent_repeatable_calls: list[str] = []
        all_displayed_text: list[str] = []
        closed = False
        terminal_status: AgentStatus | None = None
        terminal_text = ""
        terminal_error: str | None = None
        active_tool_calls: tuple[ToolCall, ...] | None = None
        paired_tool_call_indexes: set[int] = set()

        def append_unstarted_tool_pairs() -> None:
            """Close only the tool-call batch appended by this runner."""

            if active_tool_calls is None:
                return
            for index, call in enumerate(active_tool_calls):
                if index in paired_tool_call_indexes:
                    continue
                result = ToolResult(
                    ok=False,
                    code="NOT_STARTED",
                    data={"message": "此工具调用未启动，本轮已提前结束"},
                    query_id=None,
                )
                working_messages.append(result.as_model_message(call.call_id))
                paired_tool_call_indexes.add(index)

        def append_tool_result_pair(
            call: ToolCall,
            result: ToolResult,
            index: int,
            *,
            message: Mapping[str, Any] | None = None,
        ) -> None:
            if index in paired_tool_call_indexes:
                return
            working_messages.append(
                message if isinstance(message, dict) else result.as_model_message(call.call_id)
            )
            paired_tool_call_indexes.add(index)

        def query_owner_state() -> str:
            check = getattr(self.tool_registry, "query_owner_state", None)
            if not callable(check):
                return "idle"
            try:
                state = check(context)
            except Exception:
                return "failed"
            return state if state in {"idle", "pending", "failed"} else "failed"

        def query_owner_payload(state: str) -> dict[str, Any]:
            if state == "pending":
                return {
                    "context_status": "deferred",
                    "query_worker_pending": True,
                    "query_worker_status": "pending",
                }
            if state == "failed":
                return {
                    "context_status": "deferred",
                    "query_worker_pending": False,
                    "query_worker_status": "unverified",
                }
            return {}

        external_sink = on_event or self.on_event

        def record_event(event: AgentEvent) -> None:
            emitted.append(event)
            if external_sink is not None:
                try:
                    external_sink(event)
                except Exception:
                    # A UI subscriber cannot make the model/tool worker fail.
                    pass

        emitter = AgentEventEmitter(context.run_id, context.tab_id, record_event)

        def emit(event_type: str | AgentEventType, payload: Mapping[str, Any] | None = None) -> AgentEvent | None:
            if closed:
                return None
            return emitter.emit(event_type, payload)

        def budget_payload() -> dict[str, Any]:
            return run_budget.as_dict()

        def finish(
            status: AgentStatus,
            *,
            text: str = "",
            error: str | None = None,
            event_type: AgentEventType | None = None,
            payload: Mapping[str, Any] | None = None,
        ) -> AgentRunResult:
            nonlocal closed, terminal_status, terminal_text, terminal_error
            if closed:
                return AgentRunResult(
                    status=terminal_status or status,
                    final_text=terminal_text,
                    events=tuple(emitted),
                    messages=tuple(working_messages),
                    tool_results=tuple(tool_results),
                    error=terminal_error,
                    budget=budget_payload(),
                )
            append_unstarted_tool_pairs()
            terminal_status = status
            terminal_text = text
            terminal_error = error
            final_payload: dict[str, Any] = {
                "status": status.value,
                "text": text,
                "error": error,
                "budget": budget_payload(),
            }
            if payload:
                final_payload.update(dict(payload))
            if event_type is not None:
                emitter.emit(event_type, final_payload)
            release_run = getattr(self.model_adapter, "release_run", None)
            if callable(release_run):
                try:
                    release_run(context)
                except Exception:
                    # Private transport cleanup cannot change the terminal
                    # result or leak into the public event stream.
                    pass
            closed = True
            return AgentRunResult(
                status=status,
                final_text=text,
                events=tuple(emitted),
                messages=tuple(working_messages),
                tool_results=tuple(tool_results),
                error=error,
                budget=budget_payload(),
            )

        def cancellation_result() -> AgentRunResult:
            reason = token.reason or "cancelled"
            status = AgentStatus.TIMEOUT if "timeout" in reason.lower() else AgentStatus.CANCELLED
            worker_state = query_owner_state()
            worker_payload = query_owner_payload(worker_state)
            if worker_payload:
                emit(AgentEventType.BUDGET, worker_payload)
            return finish(
                status,
                text="".join(all_displayed_text),
                error=reason,
                event_type=AgentEventType.CANCELLED,
                payload={"reason": reason, "partial": True, **worker_payload},
            )

        def budget_result(reason: str | None = None, *, extra_payload: Mapping[str, Any] | None = None) -> AgentRunResult:
            actual_reason = reason or run_budget.exhausted_reason() or "budget"
            status = AgentStatus.TIMEOUT if actual_reason == "time" else AgentStatus.BUDGET_EXHAUSTED
            payload: dict[str, Any] = {"reason": actual_reason, "partial": True}
            if extra_payload:
                payload.update(dict(extra_payload))
            return finish(
                status,
                text="".join(all_displayed_text),
                error=actual_reason,
                event_type=AgentEventType.BUDGET_EXHAUSTED,
                payload=payload,
            )

        def defer_for_query_owner(state: str) -> AgentRunResult:
            payload = query_owner_payload(state)
            payload["reason"] = "query_owner_pending" if state == "pending" else "query_owner_unverified"
            emit(AgentEventType.BUDGET, payload)
            if token.is_cancelled():
                return cancellation_result()
            return budget_result(payload["reason"], extra_payload=payload)

        # Capturing definitions before the first model request keeps a broken
        # registry from causing a request that could never execute safely.
        try:
            definitions, allowed_names, enforce_closed_set = self._tool_definitions()
        except Exception as error:
            emit(AgentEventType.RUN_STARTED, {"status": AgentStatus.PREPARING.value, "context": context.as_dict()})
            return finish(
                AgentStatus.FAILED,
                error=_safe_error("TOOL_DEFINITIONS_FAILED", error),
                event_type=AgentEventType.FAILED,
            )

        emit(AgentEventType.RUN_STARTED, {
            "status": AgentStatus.PREPARING.value,
            "context": context.as_dict(),
        })

        if private_history:
            return finish(
                AgentStatus.FAILED,
                error="PRIVATE_REASONING_HISTORY",
                event_type=AgentEventType.FAILED,
            )

        try:
            for turn_index in range(run_budget.limits.max_model_turns):
                if closed:
                    break
                if token.is_cancelled():
                    return cancellation_result()

                owner_state = query_owner_state()
                if owner_state != "idle":
                    return defer_for_query_owner(owner_state)

                reason = run_budget.exhausted_reason()
                if reason is not None:
                    return budget_result(reason)

                input_repr = self.system_prompt if system_prompt is None else str(system_prompt)
                output_limit = run_budget.limits.max_output_tokens
                remaining_output = (
                    None if output_limit is None else max(0, output_limit - run_budget.output_tokens)
                )
                if remaining_output == 0:
                    return budget_result("output_tokens")
                selected_output = getattr(getattr(self.model_adapter, "snapshot", None), "max_tokens", None)
                if type(selected_output) is int and selected_output > 0:
                    remaining_output = (
                        selected_output if remaining_output is None
                        else min(remaining_output, selected_output)
                    )
                # Plan only at a completed turn boundary. Tools and confirmation
                # have finished synchronously before control can reach here.
                # Pruning the request must never erase the canonical transcript.
                max_request_bytes = None
                if self.context_planner is not None:
                    plan = self.context_planner.plan(
                        system_prompt=input_repr,
                        messages=working_messages,
                        tools=definitions,
                    )
                    emit(AgentEventType.BUDGET, {
                        "context_status": plan.status,
                        "estimate_kind": plan.estimate.kind,
                        "input_estimate": plan.estimate.tokens,
                        "context_window_known": plan.window_known,
                        "output_reserve": plan.output_reserve_tokens,
                        "messages_kept": plan.messages_kept,
                        "messages_dropped": plan.messages_dropped,
                        "reason": plan.reason,
                    })
                    if token.is_cancelled():
                        return cancellation_result()
                    if plan.status != "ready":
                        return budget_result(plan.reason or "context_budget")
                    model_messages = tuple(plan.messages)
                    max_request_bytes = plan.max_request_bytes
                    remaining_output = (
                        plan.output_reserve_tokens if remaining_output is None
                        else min(remaining_output, plan.output_reserve_tokens)
                    )
                    if remaining_output == 0:
                        return budget_result("output_tokens")
                else:
                    model_messages = tuple(deepcopy(working_messages))
                input_wire = input_repr + stable_json(model_messages) + stable_json(definitions)
                if not run_budget.add_input_chars(len(input_wire)):
                    return budget_result("input_chars")
                if not run_budget.add_bytes(input_wire):
                    return budget_result("bytes")
                try:
                    run_budget.reserve_model_turn()
                except BudgetExceeded as exhausted:
                    return budget_result(exhausted.reason)

                emit(
                    AgentEventType.TURN_STARTED,
                    {
                        "turn_index": turn_index,
                        "status": AgentStatus.MODEL_PENDING.value,
                        "budget": budget_payload(),
                    },
                )

                turn_text_parts: list[str] = []
                reasoning_parts: list[str] = []
                saw_text_delta = False
                streamed_output_tokens: int | None = None
                turn_open = True

                def on_delta(raw_delta: Any) -> None:
                    nonlocal saw_text_delta, streamed_output_tokens
                    if closed or not turn_open or token.is_cancelled():
                        return
                    if run_budget.exhausted_reason() in {"time", "bytes", "input_chars", "output_tokens"}:
                        return
                    try:
                        delta = ModelDelta.from_value(raw_delta)
                    except Exception:
                        return
                    kind = str(delta.kind or "text").strip().lower()
                    text = delta.text or ""
                    if kind in _REASONING_KINDS:
                        if not text or not run_budget.add_bytes(text):
                            return
                        reasoning_parts.append(text)
                        emit(
                            AgentEventType.MODEL_REASONING_DELTA,
                            {"turn_index": turn_index, "text": text},
                        )
                        return
                    if kind in _TEXT_KINDS or kind == "":
                        if not text or not run_budget.add_bytes(text):
                            return
                        saw_text_delta = True
                        turn_text_parts.append(text)
                        all_displayed_text.append(text)
                        emit(
                            AgentEventType.MODEL_TEXT_DELTA,
                            {"turn_index": turn_index, "text": text},
                        )
                        return
                    if kind in _TOOL_DELTA_KINDS:
                        # Tool-call fragments are deliberately buffered in the
                        # model adapter.  A partial JSON argument is never a
                        # runnable call.  The complete AgentTurn is the only
                        # boundary at which this runner schedules a tool.
                        if delta.arguments_delta:
                            run_budget.add_bytes(delta.arguments_delta)
                        return
                    if kind in _USAGE_KINDS and delta.usage:
                        tokens = delta.usage.get("completion_tokens", delta.usage.get("output_tokens"))
                        if tokens is not None:
                            try:
                                # Usage frames are usually cumulative.  Keep
                                # the value until the turn completes and
                                # account for it exactly once below.
                                parsed_tokens = int(tokens)
                                streamed_output_tokens = max(
                                    parsed_tokens,
                                    streamed_output_tokens or 0,
                                )
                            except (TypeError, ValueError):
                                pass

                request = AgentTurnRequest(
                    context=context,
                    system_prompt=input_repr,
                    messages=model_messages,
                    tools=deepcopy(definitions),
                    turn_index=turn_index,
                    max_output_tokens=remaining_output,
                    max_request_bytes=max_request_bytes,
                )
                try:
                    turn_open = True
                    raw_turn = self.model_adapter.complete_turn(
                        request,
                        cancellation=token,
                        on_delta=on_delta,
                    )
                except RequestInputLimitExceeded as error:
                    turn_open = False
                    if token.is_cancelled():
                        return cancellation_result()
                    emit(AgentEventType.BUDGET, {
                        "request_budget_status": "blocked",
                        "measurement_kind": "serialized_request_utf8_bytes",
                        "local_byte_limit": error.limit_bytes,
                    })
                    return finish(
                        AgentStatus.BUDGET_EXHAUSTED,
                        text="".join(all_displayed_text),
                        error="final_request_input_bytes",
                        event_type=AgentEventType.BUDGET_EXHAUSTED,
                        payload={"reason": "final_request_input_bytes", "partial": True},
                    )
                except Exception as error:
                    turn_open = False
                    if token.is_cancelled():
                        return cancellation_result()
                    return finish(
                        AgentStatus.FAILED,
                        text="".join(all_displayed_text),
                        error=_safe_error("MODEL_FAILED", error),
                        event_type=AgentEventType.FAILED,
                    )
                finally:
                    # An adapter that schedules a callback after returning
                    # cannot append to this completed turn.
                    turn_open = False

                if token.is_cancelled():
                    return cancellation_result()
                if run_budget.exhausted_reason() == "time":
                    return budget_result("time")

                try:
                    turn = _as_turn(raw_turn)
                except Exception as error:
                    return finish(
                        AgentStatus.FAILED,
                        text="".join(all_displayed_text),
                        error=_safe_error("MODEL_TURN_INVALID", error),
                        event_type=AgentEventType.FAILED,
                    )

                complete_text = turn.text or ""
                # Adapters often return the complete text for convenience even
                # after emitting every delta.  Use the deltas as the canonical
                # visible text in that case, preventing duplicate UI output.
                visible_text = "".join(turn_text_parts) if saw_text_delta else complete_text
                if not saw_text_delta and visible_text:
                    run_budget.add_bytes(visible_text)
                    all_displayed_text.append(visible_text)
                    # A text-only/non-streaming adapter has no incremental
                    # callback to show.  Publish one explicitly marked
                    # fallback event so an intermediate tool-turn response is
                    # visible without pretending that it was streamed.
                    emit(
                        AgentEventType.MODEL_TEXT_DELTA,
                        {"turn_index": turn_index, "text": visible_text, "streamed": False},
                    )
                usage_tokens = turn.usage.get("completion_tokens", turn.usage.get("output_tokens"))
                if usage_tokens is not None:
                    try:
                        run_budget.add_output_tokens(int(usage_tokens))
                    except (TypeError, ValueError):
                        pass
                elif streamed_output_tokens is not None:
                    run_budget.add_output_tokens(streamed_output_tokens)
                elif visible_text:
                    run_budget.add_output_tokens(_estimate_tokens(visible_text))

                if run_budget.exhausted_reason() in {"bytes", "output_tokens", "input_chars"}:
                    return budget_result(run_budget.exhausted_reason())

                calls = tuple(turn.tool_calls)
                finish_reason = str(turn.finish_reason or "stop").strip().lower()
                if not calls:
                    if finish_reason in _CLARIFY_REASONS:
                        if visible_text:
                            working_messages.append({"role": "assistant", "content": visible_text})
                        return finish(
                            AgentStatus.NEEDS_CLARIFICATION,
                            text=visible_text,
                            event_type=AgentEventType.NEEDS_CLARIFICATION,
                            payload={"question": visible_text},
                        )
                    if finish_reason in _LENGTH_REASONS:
                        if visible_text:
                            working_messages.append({"role": "assistant", "content": visible_text})
                        return budget_result("output_tokens")
                    if finish_reason in {"cancelled", "canceled", "cancel"}:
                        return cancellation_result()
                    if finish_reason in {"tool_calls", "tool_call", "function_call"}:
                        return finish(
                            AgentStatus.FAILED,
                            text="".join(all_displayed_text),
                            error="TOOL_CALL_INCOMPLETE",
                            event_type=AgentEventType.FAILED,
                        )
                    if visible_text:
                        working_messages.append({"role": "assistant", "content": visible_text})
                    query_ids = list(verified_query_ids)
                    evidence_ids = [result.evidence_id for result in tool_results if result.evidence_id]
                    return finish(
                        AgentStatus.COMPLETED,
                        text=visible_text,
                        event_type=AgentEventType.FINAL,
                        payload={
                            "model_id": turn.model_id,
                            "reasoning_summary": "".join(reasoning_parts),
                            "query_ids": query_ids,
                            "evidence_ids": evidence_ids,
                            "verified": bool(query_ids),
                            "verification_status": "verified" if query_ids else "unverified",
                        },
                    )

                # Validate all calls before executing the first one.  A single
                # unknown/conflicting request therefore cannot cause a partial
                # execution of a model turn.
                seen_turn: dict[str, tuple[str, str]] = {}
                validation_error: str | None = None
                for call in calls:
                    if not isinstance(call, ToolCall):
                        validation_error = "ARGUMENT_INVALID"
                        break
                    if not call.call_id or not call.name:
                        validation_error = "ARGUMENT_INVALID"
                        break
                    fingerprint = (call.name, call.argument_key)
                    previous = seen_turn.get(call.call_id)
                    if previous is not None and previous != fingerprint:
                        validation_error = "TOOL_CALL_CONFLICT"
                        break
                    previous_cached = call_cache.get(call.call_id)
                    if previous_cached is not None and previous_cached[0] != fingerprint:
                        validation_error = "TOOL_CALL_CONFLICT"
                        break
                    seen_turn[call.call_id] = fingerprint
                    if enforce_closed_set and call.name not in allowed_names:
                        validation_error = "POLICY_DENIED"
                        break
                if validation_error is not None:
                    status = AgentStatus.POLICY_BLOCKED if validation_error == "POLICY_DENIED" else AgentStatus.FAILED
                    return finish(
                        status,
                        text="".join(all_displayed_text),
                        error=validation_error,
                        event_type=AgentEventType.FAILED,
                        payload={"policy_blocked": status == AgentStatus.POLICY_BLOCKED},
                    )

                # Hosts may identify harmless read-only calls that can be
                # repeated without side effects.  The third identical
                # consecutive request is answered with a reminder instead of
                # being executed, which lets weaker models recover; only a
                # persistent loop (fifth identical request) stops the run.
                # Other calls break the streak, and the common runner stays
                # policy-neutral when no predicate is supplied.
                repeated_call = False
                reminder_call_ids: set[str] = set()
                for call in calls:
                    try:
                        repeatable = bool(self.is_repeatable_tool(call))
                    except Exception:
                        repeatable = False
                    if not repeatable:
                        recent_repeatable_calls.clear()
                        continue
                    fingerprint = stable_json({"name": call.name, "arguments": dict(call.arguments)})
                    repeat_count = 0
                    for previous in reversed(recent_repeatable_calls):
                        if previous != fingerprint:
                            break
                        repeat_count += 1
                    if repeat_count >= _REPEAT_STOP_AFTER:
                        repeated_call = True
                        break
                    if repeat_count >= _REPEAT_REMIND_AFTER:
                        reminder_call_ids.add(call.call_id)
                    recent_repeatable_calls.append(fingerprint)
                if repeated_call:
                    return finish(
                        AgentStatus.FAILED,
                        text="".join(all_displayed_text),
                        error="REPEATED_TOOL_CALL",
                        event_type=AgentEventType.FAILED,
                        payload={"loop_guard": True},
                    )

                if self.authorize_tool_batch is not None:
                    try:
                        authorized = self.authorize_tool_batch(calls, context, token)
                    except Exception as error:
                        return finish(
                            AgentStatus.FAILED,
                            text="".join(all_displayed_text),
                            error=_safe_error("TOOL_AUTHORIZATION_FAILED", error),
                            event_type=AgentEventType.FAILED,
                        )
                    if token.is_cancelled():
                        return cancellation_result()
                    if not authorized:
                        return finish(
                            AgentStatus.CANCELLED,
                            text="".join(all_displayed_text),
                            error="USER_CANCELLED_PLAN",
                            event_type=AgentEventType.CANCELLED,
                            payload={"reason": "USER_CANCELLED_PLAN", "partial": True},
                        )

                # A clarification request is a control message. It stops the
                # run before invoking the injected registry so the host can
                # collect a user choice and start a fresh run.
                clarification = next(
                    (call for call in calls if call.name == "request_clarification"),
                    None,
                )
                if clarification is not None:
                    question = str(clarification.arguments.get("question") or "").strip()
                    if not question:
                        return finish(
                            AgentStatus.POLICY_BLOCKED,
                            text="".join(all_displayed_text),
                            error="ARGUMENT_INVALID",
                            event_type=AgentEventType.FAILED,
                            payload={"policy_blocked": True},
                        )
                    candidates = clarification.arguments.get("candidate_ids", ())
                    if not isinstance(candidates, (list, tuple)):
                        candidates = ()
                    emit(
                        AgentEventType.TOOL_CALL,
                        {
                            "turn_index": turn_index,
                            "call_id": clarification.call_id,
                            "name": clarification.name,
                            "arguments": dict(clarification.arguments),
                        },
                    )
                    return finish(
                        AgentStatus.NEEDS_CLARIFICATION,
                        text=question,
                        event_type=AgentEventType.NEEDS_CLARIFICATION,
                        payload={
                            "question": question,
                            "candidate_ids": list(candidates),
                            "call_id": clarification.call_id,
                        },
                    )

                # Preserve assistant.tool_calls before any tool result is added;
                # the next model request then has a valid OpenAI-style pair.
                # A text-protocol recovery replaces the streamed markup with the
                # prose it wants kept.  Prefer that text over the raw deltas.
                stored_text = complete_text if calls and complete_text != visible_text else visible_text
                assistant_call_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": stored_text,
                    "tool_calls": [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": stable_json(call.arguments),
                            },
                        }
                        for call in calls
                    ],
                }
                working_messages.append(assistant_call_message)
                active_tool_calls = calls
                paired_tool_call_indexes = set()
                batch_commit_records: list[dict[str, Any]] = []

                for call_index, call in enumerate(calls):
                    if token.is_cancelled():
                        return cancellation_result()
                    owner_state = query_owner_state()
                    if owner_state != "idle":
                        return defer_for_query_owner(owner_state)
                    emit(
                        AgentEventType.TOOL_CALL,
                        {"turn_index": turn_index, "call_id": call.call_id, "name": call.name, "arguments": dict(call.arguments)},
                    )

                    if call.call_id in reminder_call_ids:
                        reminder = ToolResult(ok=False, code="REPEATED_CALL", data={"message": _REPEAT_REMINDER})
                        append_tool_result_pair(call, reminder, call_index)
                        emit(
                            AgentEventType.TOOL_RESULT,
                            {"turn_index": turn_index, "call_id": call.call_id, "name": call.name, **reminder.as_dict()},
                        )
                        continue

                    cached = call_cache.get(call.call_id)
                    if cached is not None:
                        result = cached[1]
                        append_tool_result_pair(call, result, call_index)
                        emit(
                            AgentEventType.TOOL_RESULT,
                            {
                                "turn_index": turn_index,
                                "call_id": call.call_id,
                                "name": call.name,
                                "reused": True,
                                **result.as_dict(),
                            },
                        )
                        continue

                    try:
                        run_budget.reserve_tool_call()
                        if self.is_query_tool(call):
                            run_budget.reserve_query()
                    except BudgetExceeded as exhausted:
                        return budget_result(exhausted.reason)

                    emit(
                        AgentEventType.TOOL_STARTED,
                        {
                            "turn_index": turn_index,
                            "call_id": call.call_id,
                            "name": call.name,
                            "status": AgentStatus.TOOL_RUNNING.value,
                            "budget": budget_payload(),
                        },
                    )
                    try:
                        raw_result = self.tool_registry.execute(
                            call,
                            context=context,
                            cancellation=token,
                        )
                        result = ToolResult.from_value(raw_result)
                    except Exception as error:
                        result = ToolResult(ok=False, code="TOOL_FAILED", data=None)
                        tool_error = _safe_error("TOOL_EXECUTION_FAILED", error)
                    else:
                        tool_error = None

                    # Preserve safe timeout bookkeeping, but a late success
                    # cannot revive a cancelled run or enter its evidence/cache.
                    if token.is_cancelled():
                        if not result.ok and result.code in {"TIMEOUT", "CANCELLED"}:
                            safe_result = ToolResult(
                                ok=False,
                                code=result.code,
                                query_id=result.query_id,
                            )
                            tool_results.append(safe_result)
                            append_tool_result_pair(call, safe_result, call_index)
                        else:
                            append_tool_result_pair(
                                call,
                                ToolResult(
                                    ok=False,
                                    code="CANCELLED",
                                    data={"message": "取消后到达的工具结果已抑制"},
                                    query_id=None,
                                ),
                                call_index,
                            )
                        return cancellation_result()
                    call_cache[call.call_id] = ((call.name, call.argument_key), result)
                    if result.ok or result.code:
                        tool_results.append(result)
                    if (
                        result.ok
                        and result.query_id
                        and self.is_query_tool(call)
                        and call.name.strip().casefold()
                        not in {"search_objects", "describe_object"}
                    ):
                        verified_query_ids.append(str(result.query_id))
                    result_message = result.as_model_message(call.call_id)
                    append_tool_result_pair(call, result, call_index, message=result_message)
                    if result.ok:
                        # This detached copy contains only the exact public
                        # message appended above; tool arguments and native
                        # result objects never cross the optional capability.
                        batch_commit_records.append({
                            "call_id": call.call_id,
                            "name": call.name,
                            "message": deepcopy(result_message),
                        })
                    result_wire = result_message.get("content", "")
                    run_budget.add_bytes(result_wire)
                    emit(
                        AgentEventType.TOOL_RESULT,
                        {
                            "turn_index": turn_index,
                            "call_id": call.call_id,
                            "name": call.name,
                            "reused": False,
                            "error": tool_error,
                            **result.as_dict(),
                        },
                    )
                    emit(AgentEventType.BUDGET, {"budget": budget_payload()})
                    owner_state = query_owner_state()
                    if owner_state != "idle":
                        return defer_for_query_owner(owner_state)
                    if run_budget.exhausted_reason() in {"bytes", "time", "input_chars", "output_tokens"}:
                        return budget_result(run_budget.exhausted_reason())

                # Publish candidates only after every call in this batch has
                # a canonical result pair and the terminal gates still allow
                # the run to continue. Any earlier return discards this local
                # batch automatically.
                if paired_tool_call_indexes != set(range(len(calls))):
                    return finish(
                        AgentStatus.FAILED,
                        error="TOOL_BATCH_INCOMPLETE",
                        event_type=AgentEventType.FAILED,
                    )
                if token.is_cancelled():
                    return cancellation_result()
                owner_state = query_owner_state()
                if owner_state != "idle":
                    return defer_for_query_owner(owner_state)
                reason = run_budget.exhausted_reason()
                if reason is not None:
                    return budget_result(reason)
                if commit_tool_batch is not None and batch_commit_records:
                    try:
                        commit_tool_batch(tuple(batch_commit_records))
                    except Exception:
                        # Evidence is optional. A broken hook must not change
                        # the already paired canonical result or run outcome.
                        disable_tool_evidence()

                active_tool_calls = None
                paired_tool_call_indexes = set()

            # ``range`` is bounded by the immutable limit.  Normally this is
            # reached through the next iteration's budget check, but keeping a
            # terminal branch here makes a zero-message fake deterministic.
            return budget_result("model_turns")
        except BudgetExceeded as exhausted:
            return budget_result(exhausted.reason)
        except Exception as error:
            if token.is_cancelled():
                return cancellation_result()
            return finish(
                AgentStatus.FAILED,
                text="".join(all_displayed_text),
                error=_safe_error("RUN_FAILED", error),
                event_type=AgentEventType.FAILED,
            )


def run_agent(
    context: RunContext,
    user_message: str,
    model_adapter: ModelAdapter,
    tool_registry: ToolRegistry,
    **kwargs: Any,
) -> AgentRunResult:
    """Functional convenience wrapper around :class:`AgentRunner`."""

    return AgentRunner(model_adapter, tool_registry, **kwargs).run(context, user_message)


# Names used by early host prototypes; they remain aliases, not separate
# implementations, so there is still one audited runner.
DataCenterAgent = AgentRunner
Agent = AgentRunner


__all__ = ["Agent", "AgentRunner", "DataCenterAgent", "run_agent"]
