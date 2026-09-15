"""Pure-Python multi-turn runner for the data-center read-only Agent.

This module intentionally knows nothing about Qt, sockets, database drivers,
or the existing programming Agent.  A model adapter and a closed tool
registry are injected by the host.  The runner owns only orchestration:
messages are paired by ``tool_call_id``, a completed call is idempotent within
one run, and cancellation closes the run so callbacks/results that arrive
later cannot mutate it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
import threading
from typing import Any, Callable

from .budget import Budget, BudgetExceeded, BudgetLimits
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
_QUERY_TOOL_NAMES = frozenset(
    {
        "query_readonly",
        "query_read_only",
        "sql_query",
        "read_query",
        "mongo_read",
        "redis_read",
    }
)


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


def _is_query_call(call: ToolCall) -> bool:
    name = call.name.strip().lower()
    if name in _QUERY_TOOL_NAMES:
        return True
    if name.startswith("query_") or name.endswith("_query"):
        return True
    if "query" in name and any(word in name for word in ("read", "select", "execute")):
        return True
    return False


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
    ) -> None:
        self.model_adapter = model_adapter or model
        self.tool_registry = tool_registry or tools
        self.system_prompt = str(system_prompt)
        self.default_budget = budget
        self.budget_limits = budget_limits
        self.cancellation = cancellation or CancellationToken()
        self.on_event = on_event or event_sink

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

        if isinstance(context, str) and isinstance(user_message, RunContext):
            context, user_message = user_message, context
        if not isinstance(context, RunContext):
            raise TypeError("context must be a RunContext")
        if user_message is not None and not isinstance(user_message, str):
            raise TypeError("user_message must be a string")
        if self.model_adapter is None:
            raise RuntimeError("model adapter is not configured")

        token = cancellation or self.cancellation
        self.cancellation = token
        run_budget = self._new_budget(budget)
        source_messages = messages if messages is not None else initial_messages
        working_messages: list[dict[str, Any]] = [dict(item) for item in (source_messages or ())]
        question = user_message or ""
        if question and not (
            working_messages
            and working_messages[-1].get("role") == "user"
            and working_messages[-1].get("content") == question
        ):
            working_messages.append({"role": "user", "content": question})

        emitted: list[AgentEvent] = []
        tool_results: list[ToolResult] = []
        call_cache: dict[str, tuple[tuple[str, str], ToolResult]] = {}
        all_displayed_text: list[str] = []
        closed = False
        terminal_status: AgentStatus | None = None
        terminal_text = ""
        terminal_error: str | None = None

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
            return finish(
                status,
                text="".join(all_displayed_text),
                error=reason,
                event_type=AgentEventType.CANCELLED,
                payload={"reason": reason, "partial": True},
            )

        def budget_result(reason: str | None = None) -> AgentRunResult:
            actual_reason = reason or run_budget.exhausted_reason() or "budget"
            status = AgentStatus.TIMEOUT if actual_reason == "time" else AgentStatus.BUDGET_EXHAUSTED
            return finish(
                status,
                text="".join(all_displayed_text),
                error=actual_reason,
                event_type=AgentEventType.BUDGET_EXHAUSTED,
                payload={"reason": actual_reason, "partial": True},
            )

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

        emit(
            AgentEventType.RUN_STARTED,
            {
                "status": AgentStatus.PREPARING.value,
                "run_id": context.run_id,
                "tab_id": context.tab_id,
                "model_config_id": context.model_config_id,
                "connection_id": context.connection_id,
                "database": context.database,
                "schema_allowlist": list(context.schema_allowlist),
                "profile_revision": context.profile_revision,
                "policy_version": context.policy_version,
            },
        )

        try:
            for turn_index in range(run_budget.limits.max_model_turns):
                if closed:
                    break
                if token.is_cancelled():
                    return cancellation_result()

                reason = run_budget.exhausted_reason()
                if reason is not None:
                    return budget_result(reason)

                model_messages = tuple(dict(item) for item in working_messages)
                input_repr = self.system_prompt if system_prompt is None else str(system_prompt)
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
                    tools=definitions,
                    turn_index=turn_index,
                    max_output_tokens=run_budget.limits.max_output_tokens,
                )
                try:
                    turn_open = True
                    raw_turn = self.model_adapter.complete_turn(
                        request,
                        cancellation=token,
                        on_delta=on_delta,
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
                    query_ids = [result.query_id for result in tool_results if result.query_id]
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

                # Clarification is a control message, not a database action.
                # It deliberately stops the run before invoking the injected
                # registry, leaving the host to start a fresh run after the
                # user selects one of the candidates.
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
                assistant_call_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": visible_text,
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

                for call in calls:
                    if token.is_cancelled():
                        return cancellation_result()
                    emit(
                        AgentEventType.TOOL_CALL,
                        {"turn_index": turn_index, "call_id": call.call_id, "name": call.name, "arguments": dict(call.arguments)},
                    )

                    cached = call_cache.get(call.call_id)
                    if cached is not None:
                        result = cached[1]
                        working_messages.append(result.as_model_message(call.call_id))
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
                        if _is_query_call(call):
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

                    # A canceled/closed run cannot be revived by a late query
                    # result.  It is deliberately absent from both event and
                    # model message streams.
                    if token.is_cancelled():
                        return cancellation_result()
                    call_cache[call.call_id] = ((call.name, call.argument_key), result)
                    if result.ok or result.code:
                        tool_results.append(result)
                    result_message = result.as_model_message(call.call_id)
                    working_messages.append(result_message)
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
                    if run_budget.exhausted_reason() in {"bytes", "time", "input_chars", "output_tokens"}:
                        return budget_result(run_budget.exhausted_reason())

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
