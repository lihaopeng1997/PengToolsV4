"""Observable tests for the generic Runner's optional batch commit hook."""

from __future__ import annotations

from copy import deepcopy
import unittest

from tools.agent_core.contracts import AgentStatus, AgentTurn, RunContext, ToolCall, ToolResult
from tools.agent_core.budget import BudgetLimits
from tools.agent_core.runner import AgentRunner


def _context() -> RunContext:
    return RunContext("hook-run", "hook-tab", "synthetic-config")


class _Model:
    def __init__(self, *turns: AgentTurn) -> None:
        self.turns = list(turns)
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("unexpected model request")
        return self.turns.pop(0)


class _Registry:
    def __init__(self, results: dict[str, ToolResult], *, owner_after_execute: str = "idle") -> None:
        self.results = results
        self.owner_after_execute = owner_after_execute
        self.owner = "idle"
        self.calls = []
        self.begin_count = 0
        self.batches = []
        self.disabled = 0

    def definitions(self):
        return ({"name": "lookup", "description": "synthetic"},)

    def query_owner_state(self, context):  # noqa: ARG002
        return self.owner

    def execute(self, call, *, context, cancellation):  # noqa: ARG002
        self.calls.append(call)
        self.owner = self.owner_after_execute
        return self.results.get(call.call_id, ToolResult(ok=True, code="OK", data={"call": call.call_id}))

    def begin_tool_commit(self, context):
        self.begin_count += 1
        captured = context

        def commit(records):
            self.batches.append((captured, deepcopy(tuple(records))))

        def disable():
            self.disabled += 1

        commit.disable_evidence = disable
        return commit


class AgentToolCommitHookTests(unittest.TestCase):
    def test_only_fresh_successes_are_sent_after_the_whole_batch_is_paired(self) -> None:
        model = _Model(
            AgentTurn(
                tool_calls=(
                    ToolCall("fresh-ok", "lookup", {"private_argument": "never forwarded"}),
                    ToolCall("fresh-fail", "lookup", {"other": "private"}),
                ),
                finish_reason="tool_calls",
            ),
            AgentTurn(text="done"),
        )
        registry = _Registry({
            "fresh-ok": ToolResult(ok=True, code="OK", data={"public": "value"}),
            "fresh-fail": ToolResult(ok=False, code="TOOL_FAILED", data={"message": "safe"}),
        })

        result = AgentRunner(model, registry).run(_context(), "question")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(registry.begin_count, 1)
        self.assertEqual(len(registry.batches), 1)
        context, records = registry.batches[0]
        self.assertEqual(context.run_id, "hook-run")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(set(record), {"call_id", "name", "message"})
        self.assertEqual(record["call_id"], "fresh-ok")
        self.assertEqual(record["name"], "lookup")
        canonical = next(
            message for message in result.messages
            if message.get("role") == "tool" and message.get("tool_call_id") == "fresh-ok"
        )
        self.assertEqual(record["message"], canonical)
        self.assertIsNot(record["message"], canonical)
        self.assertNotIn("arguments", record)
        self.assertNotIn("private_argument", str(record))
        self.assertTrue(all(
            message.get("role") == "tool"
            for message in result.messages
            if message.get("tool_call_id") in {"fresh-ok", "fresh-fail"}
        ))

    def test_cache_reuse_and_terminal_owner_deferral_do_not_commit_again(self) -> None:
        repeated = ToolCall("same-id", "lookup", {"x": 1})
        model = _Model(
            AgentTurn(tool_calls=(repeated,), finish_reason="tool_calls"),
            AgentTurn(tool_calls=(repeated,), finish_reason="tool_calls"),
            AgentTurn(text="done"),
        )
        registry = _Registry({"same-id": ToolResult(ok=True, code="OK", data="value")})
        result = AgentRunner(model, registry).run(_context(), "question")
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(registry.calls), 1)
        self.assertEqual(len(registry.batches), 1)

        deferred_model = _Model(
            AgentTurn(tool_calls=(ToolCall("pending", "lookup"),), finish_reason="tool_calls")
        )
        pending_registry = _Registry(
            {"pending": ToolResult(ok=True, code="OK", data="value")},
            owner_after_execute="pending",
        )
        deferred = AgentRunner(deferred_model, pending_registry).run(_context(), "question")
        self.assertEqual(deferred.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(pending_registry.batches, [])
        self.assertTrue(any(
            item.get("tool_call_id") == "pending" for item in deferred.messages
        ))

    def test_throwing_commit_isolated_and_clears_the_bound_evidence_capability(self) -> None:
        model = _Model(
            AgentTurn(tool_calls=(ToolCall("call", "lookup"),), finish_reason="tool_calls"),
            AgentTurn(text="ordinary result preserved"),
        )
        registry = _Registry({"call": ToolResult(ok=True, code="OK", data="public")})
        original = registry.begin_tool_commit

        def begin(context):
            capability = original(context)

            def fail(_records):
                raise RuntimeError("synthetic hook failure")

            fail.disable_evidence = capability.disable_evidence
            return fail

        registry.begin_tool_commit = begin
        result = AgentRunner(model, registry).run(_context(), "question")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.final_text, "ordinary result preserved")
        self.assertEqual(registry.disabled, 1)
        self.assertTrue(any(
            item.get("tool_call_id") == "call" for item in result.messages
        ))

    def test_cancellation_after_execute_discards_fresh_candidate(self) -> None:
        model = _Model(
            AgentTurn(tool_calls=(ToolCall("late", "lookup"),), finish_reason="tool_calls")
        )
        registry = _Registry({"late": ToolResult(ok=True, code="OK", data="late")})
        from tools.agent_core.contracts import CancellationToken

        cancellation = CancellationToken()
        original_execute = registry.execute

        def cancel_after_execute(call, *, context, cancellation):
            value = original_execute(call, context=context, cancellation=cancellation)
            cancellation.cancel("cancelled after fake result")
            return value

        registry.execute = cancel_after_execute
        result = AgentRunner(model, registry).run(
            _context(), "question", cancellation=cancellation
        )
        self.assertEqual(result.status, AgentStatus.CANCELLED)
        self.assertEqual(registry.batches, [])
        self.assertTrue(any(
            item.get("tool_call_id") == "late" for item in result.messages
        ))

    def test_initial_history_and_unstarted_budget_calls_never_commit(self) -> None:
        initial_history = [
            {"role": "user", "content": "earlier question"},
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "initial-call",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "initial-call", "content": "{\"ok\":true}"},
        ]
        history_model = _Model(AgentTurn(text="answer", finish_reason="stop"))
        history_registry = _Registry({})
        history_result = AgentRunner(history_model, history_registry).run(
            _context(), "current question", messages=initial_history
        )
        self.assertEqual(history_result.status, AgentStatus.COMPLETED)
        self.assertEqual(history_registry.batches, [])

        budget_model = _Model(AgentTurn(
            tool_calls=(ToolCall("started", "lookup"), ToolCall("not-started", "lookup")),
            finish_reason="tool_calls",
        ))
        budget_registry = _Registry({
            "started": ToolResult(ok=True, code="OK", data={"public": "completed"}),
        })
        budget_result = AgentRunner(
            budget_model,
            budget_registry,
            budget_limits=BudgetLimits(max_tool_calls=1),
        ).run(_context(), "question")
        self.assertEqual(budget_result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(budget_registry.batches, [])
        self.assertEqual(len(budget_registry.calls), 1)
        self.assertTrue(any(
            item.get("tool_call_id") == "not-started" for item in budget_result.messages
        ))

    def test_registry_without_the_optional_capability_keeps_old_behavior(self) -> None:
        class PlainRegistry:
            def definitions(self):
                return ({"name": "lookup"},)

            def execute(self, call, *, context, cancellation):  # noqa: ARG002
                return ToolResult(ok=True, data={"ok": True})

        model = _Model(
            AgentTurn(tool_calls=(ToolCall("plain", "lookup"),), finish_reason="tool_calls"),
            AgentTurn(text="done"),
        )
        result = AgentRunner(model, PlainRegistry()).run(_context(), "question")
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertTrue(any(item.get("tool_call_id") == "plain" for item in result.messages))


if __name__ == "__main__":
    unittest.main()
