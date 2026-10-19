"""Runtime-level tests for the built-in Data Center context planner."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import threading
import unittest
from unittest.mock import patch

from tools.agent_core.budget import Budget, BudgetLimits
from tools.agent_core.context import ContextLimits, ContextPlanner
from tools.data_center.contracts import AgentStatus, AgentTurn, RunContext, ToolCall, ToolResult, stable_json
from tools.data_center.host_runtime import DataCenterAgentRuntime


class _FakeModel:
    def __init__(self, *turns: AgentTurn) -> None:
        self.turns = list(turns)
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("unexpected fake model request")
        return self.turns.pop(0)


class _FakeMetadataHost:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, call, *, context, cancellation, deadline=None):  # noqa: ARG002
        self.calls.append(call)
        call_id = call.call_id if isinstance(call, ToolCall) else str(call.get("call_id", ""))
        return ToolResult(
            ok=True,
            code="OK",
            data={"objects": []},
            query_id=call_id,
            scope=context.as_dict(),
        )


class _FakeQueryTool:
    verified_readonly = True

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, call, *, context, cancellation):  # noqa: ARG002
        self.calls.append(call)
        call_id = call.call_id if isinstance(call, ToolCall) else str(call.get("call_id", ""))
        return ToolResult(ok=True, query_id=call_id, data={"rows": []}, scope=context.as_dict())


class _StrictRunnerFactory:
    def __init__(self) -> None:
        self.calls = []

    def __call__(
        self,
        model_adapter,
        tool_registry,
        *,
        system_prompt,
        budget,
        budget_limits,
        cancellation,
        on_event,
    ):
        call = {
            "model_adapter": model_adapter,
            "tool_registry": tool_registry,
            "system_prompt": system_prompt,
            "budget": budget,
            "budget_limits": budget_limits,
            "cancellation": cancellation,
            "on_event": on_event,
        }
        self.calls.append(call)
        return object()


class DataCenterContextPlannerTests(unittest.TestCase):
    _DENIED_TARGETS = (
        ("profile_connect", "tools.data_center.readonly_lease.ReadOnlyLeaseFactory.connect"),
        ("catalog_execute", "tools.data_center.host_metadata.MetadataHost.execute"),
        ("urlopen", "urllib.request.urlopen"),
        ("socket_create_connection", "socket.create_connection"),
        ("socket_connect", "socket.socket.connect"),
        ("model_config_client", "tools.data_center.host_runtime.AgentModelHostAdapter"),
        ("metadata_client", "tools.data_center.host_runtime.MetadataHost"),
        ("query_client", "tools.data_center.host_runtime.QueryExecutor"),
        ("nosql_client", "tools.data_center.host_runtime.HostNoSQLRouter"),
        ("profile_loader", "tools.data_center.host_model_adapter.load_agent_model_profile"),
    )

    def setUp(self) -> None:
        self.runtimes: list[DataCenterAgentRuntime] = []
        self._gate_stack = ExitStack()
        self._gates = {}
        for name, target in self._DENIED_TARGETS:
            def denied(*args, _name=name, **kwargs):  # noqa: ARG001
                raise AssertionError(f"denied external boundary reached: {_name}")

            self._gates[name] = self._gate_stack.enter_context(
                patch(target, side_effect=denied)
            )

    def tearDown(self) -> None:
        try:
            for runtime in self.runtimes:
                runtime.close()
            for name, gate in self._gates.items():
                self.assertEqual(gate.call_count, 0, f"external gate used: {name}")
        finally:
            self._gate_stack.close()

    @staticmethod
    def _context(*, draft: bool = False, run_id: str = "run-planner") -> RunContext:
        return RunContext(
            run_id=run_id,
            tab_id="tab-planner",
            model_config_id="synthetic-model-config",
            connection_id="synthetic-connection",
            database="synthetic-db",
            schema_allowlist=("synthetic-schema",),
            profile_revision="synthetic-revision",
            intent="draft" if draft else "query",
        )

    def _runtime(
        self,
        *,
        model: _FakeModel | None = None,
        metadata_host: _FakeMetadataHost | None = None,
        query_tool: _FakeQueryTool | None = None,
        events=None,
        draft: bool = False,
        runner_factory=None,
        context: RunContext | None = None,
    ) -> DataCenterAgentRuntime:
        runtime = DataCenterAgentRuntime(
            context or self._context(draft=draft),
            provider=None if draft else "mysql",
            mode="" if draft else "mysql",
            model=model or _FakeModel(AgentTurn(text="synthetic answer")),
            metadata_host=None if draft else (metadata_host or _FakeMetadataHost()),
            query_tool=None if draft else (query_tool or _FakeQueryTool()),
            system_prompt="Use only the synthetic test scope.",
            draft=draft,
            runner_factory=runner_factory,
            on_event=events.append if events is not None else None,
        )
        self.runtimes.append(runtime)
        return runtime

    def test_default_active_runtime_emits_only_public_heuristic_plan_scalars(self) -> None:
        events = []
        model = _FakeModel(AgentTurn(text="synthetic answer"))
        runtime = self._runtime(model=model, events=events)

        planner = runtime.runner.context_planner
        self.assertIsInstance(planner, ContextPlanner)
        self.assertEqual(
            planner.limits,
            ContextLimits(
                input_token_limit=64_000,
                context_window_tokens=None,
                output_reserve_tokens=4_096,
            ),
        )
        self.assertEqual(planner.estimator.kind, "heuristic_utf8_bytes")
        self.assertEqual(runtime.runner._new_budget(None).limits.max_input_chars, 24_000)

        result = runtime.run("synthetic question")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(model.requests), 1)
        request = model.requests[0]
        self.assertEqual(request.max_request_bytes, 64_000)
        self.assertEqual(request.max_output_tokens, 4_096)
        record = next(
            event.payload for event in events
            if event.event_type == "budget" and "context_status" in event.payload
        )
        self.assertEqual(record["context_status"], "ready")
        self.assertEqual(record["estimate_kind"], "heuristic_utf8_bytes")
        self.assertIs(type(record["input_estimate"]), int)
        self.assertIs(record["context_window_known"], False)
        self.assertEqual(record["output_reserve"], 4_096)
        self.assertEqual(record["messages_kept"], 1)
        self.assertEqual(record["messages_dropped"], 0)
        self.assertEqual(
            set(record),
            {
                "context_status", "estimate_kind", "input_estimate",
                "context_window_known", "output_reserve", "messages_kept",
                "messages_dropped", "reason",
            },
        )
        self.assertTrue(all(type(value) in {str, int, bool} for value in record.values()))
        self.assertNotIn("messages", record)
        self.assertNotIn("system_prompt", record)
        self.assertNotIn("reasoning_content", record)

    def test_default_planner_drops_old_exchange_only_from_request_not_canonical_history(self) -> None:
        old_payload = "synthetic-old-result " * 4_000
        history = [
            {"role": "system", "content": "synthetic history anchor"},
            {"role": "user", "content": "synthetic first question"},
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "old-call",
                    "type": "function",
                    "function": {"name": "search_objects", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "old-call", "content": old_payload},
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "new-call",
                    "type": "function",
                    "function": {"name": "search_objects", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "new-call", "content": "synthetic-new-result"},
            {"role": "user", "content": "synthetic current question"},
        ]
        original_history = [dict(message) for message in history]
        events = []
        model = _FakeModel(AgentTurn(text="synthetic answer"))
        runtime = self._runtime(model=model, events=events)

        result = runtime.run("synthetic current question", messages=history)

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(model.requests), 1)
        request_messages = model.requests[0].messages
        self.assertFalse(any(message.get("tool_call_id") == "old-call" for message in request_messages))
        self.assertFalse(any(
            any(call.get("id") == "old-call" for call in message.get("tool_calls", ()))
            for message in request_messages
        ))
        self.assertTrue(any(message.get("tool_call_id") == "new-call" for message in request_messages))
        self.assertTrue(any(
            any(call.get("id") == "new-call" for call in message.get("tool_calls", ()))
            for message in request_messages
        ))
        self.assertIn("synthetic current question", [message.get("content") for message in request_messages])
        self.assertEqual(result.messages[:-1], tuple(original_history))
        self.assertEqual(history, original_history)
        self.assertEqual(result.messages[-1], {"role": "assistant", "content": "synthetic answer"})
        record = next(
            event.payload for event in events
            if event.event_type == "budget" and "context_status" in event.payload
        )
        self.assertEqual(record["messages_dropped"], 2)
        self.assertNotIn(old_payload, stable_json(request_messages))
        self.assertEqual(model.requests[0].max_request_bytes, 64_000)

    def test_draft_and_strict_custom_factory_keep_the_old_constructor_arguments(self) -> None:
        draft_runtime = self._runtime(draft=True, model=_FakeModel(AgentTurn(text="draft")))
        self.assertIsNone(draft_runtime.runner.context_planner)

        factory = _StrictRunnerFactory()
        custom_runtime = self._runtime(
            runner_factory=factory,
            model=_FakeModel(AgentTurn(text="unused")),
        )
        self.assertEqual(len(factory.calls), 1)
        call = factory.calls[0]
        self.assertEqual(
            set(call),
            {
                "model_adapter", "tool_registry", "system_prompt", "budget",
                "budget_limits", "cancellation", "on_event",
            },
        )
        self.assertNotIn("context_planner", call)
        self.assertIs(call["model_adapter"], custom_runtime.model_adapter)
        self.assertIs(call["tool_registry"], custom_runtime.registry)

    def test_pending_and_unverified_owner_states_block_planning_and_model(self) -> None:
        query_call = ToolCall("blocked-query", "query_readonly", {"sql": "SELECT 1", "row_limit": 1})

        release = threading.Event()
        started = threading.Event()

        def block_worker():
            started.set()
            release.wait()
            return "released"

        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(block_worker)
        pending_query_tool = _FakeQueryTool()
        try:
            self.assertTrue(started.wait(1.0))
            self.assertFalse(future.done())
            pending_model = _FakeModel(AgentTurn(tool_calls=(query_call,), finish_reason="tool_calls"))
            pending_runtime = self._runtime(
                model=pending_model,
                query_tool=pending_query_tool,
                events=[],
            )
            tracker = pending_runtime.query_owner_tracker
            self.assertTrue(tracker.expect_query(pending_runtime.context, "pending-query"))
            future.query_id = "pending-query"
            future.session_id = pending_runtime.context.tab_id
            future.generation = 1
            self.assertTrue(tracker.register_query_owner(
                pending_runtime.context,
                "pending-query",
                session_id=pending_runtime.context.tab_id,
                generation=1,
                future=future,
            ))
            self.assertEqual(pending_runtime.registry.query_owner_state(pending_runtime.context), "pending")
            planner_calls = []
            original_plan = pending_runtime.runner.context_planner.plan
            pending_runtime.runner.context_planner.plan = lambda **kwargs: (
                planner_calls.append(kwargs) or original_plan(**kwargs)
            )

            pending_result = pending_runtime.run("synthetic question")

            self.assertEqual(pending_result.error, "query_owner_pending")
            self.assertTrue(any(
                event.payload.get("query_worker_pending") is True
                for event in pending_result.events
            ))
            self.assertEqual(planner_calls, [])
            self.assertEqual(pending_model.requests, [])
            self.assertEqual(pending_query_tool.calls, [])
            self.assertEqual(pending_runtime.query_owner_tracker.state(pending_runtime.context), "pending")
            release.set()
            self.assertEqual(future.result(timeout=2.0), "released")
            self.assertEqual(pending_runtime.registry.query_owner_state(pending_runtime.context), "idle")
        finally:
            release.set()
            pool.shutdown(wait=True, cancel_futures=False)

        for state_name, state_value in (
            ("failed", "failed"),
            ("invalid", "not-a-supported-state"),
            ("unknown", None),
        ):
            with self.subTest(owner_state=state_name):
                model = _FakeModel(AgentTurn(tool_calls=(query_call,), finish_reason="tool_calls"))
                query_tool = _FakeQueryTool()
                runtime = self._runtime(model=model, query_tool=query_tool, events=[])
                tracker = runtime.query_owner_tracker
                if state_name == "failed":
                    tracker.fail_query_owner(runtime.context, f"{state_name}-query")
                elif state_name == "invalid":
                    tracker.state = lambda _context: state_value
                else:
                    def broken_state(_context):
                        raise RuntimeError("synthetic owner state failure")

                    tracker.state = broken_state
                planner_calls = []
                original_plan = runtime.runner.context_planner.plan
                runtime.runner.context_planner.plan = lambda **kwargs: (
                    planner_calls.append(kwargs) or original_plan(**kwargs)
                )

                result = runtime.run("synthetic question")

                self.assertEqual(result.error, "query_owner_unverified")
                self.assertTrue(any(
                    event.payload.get("query_worker_status") == "unverified"
                    for event in result.events
                ))
                self.assertEqual(planner_calls, [])
                self.assertEqual(model.requests, [])
                self.assertEqual(query_tool.calls, [])

    def test_cumulative_input_budget_stops_second_turn_before_an_extra_request(self) -> None:
        metadata_host = _FakeMetadataHost()
        model = _FakeModel(
            AgentTurn(
                tool_calls=(ToolCall("metadata-call", "search_objects", {"keyword": "synthetic"}),),
                finish_reason="tool_calls",
            )
        )
        runtime = self._runtime(model=model, metadata_host=metadata_host)
        definitions, _, _ = runtime.runner._tool_definitions()
        first_input = (
            runtime.runner.system_prompt
            + stable_json(({"role": "user", "content": "synthetic question"},))
            + stable_json(definitions)
        )
        budget = Budget(BudgetLimits(max_input_chars=len(first_input) + 1))

        result = runtime.run("synthetic question", budget=budget)

        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.error, "input_chars")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(metadata_host.calls), 1)
        self.assertEqual(budget.snapshot().input_chars, len(first_input) + 1)

    def test_oversized_anchor_blocks_before_model_transport_or_query(self) -> None:
        query_tool = _FakeQueryTool()
        model = _FakeModel(
            AgentTurn(
                tool_calls=(ToolCall("never-query", "query_readonly", {"sql": "SELECT 1", "row_limit": 1}),),
                finish_reason="tool_calls",
            )
        )
        runtime = self._runtime(model=model, query_tool=query_tool)

        result = runtime.run("synthetic-anchor-" + ("x" * 70_000))

        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.error, "context_anchors_too_large")
        self.assertEqual(model.requests, [])
        self.assertEqual(query_tool.calls, [])


if __name__ == "__main__":
    unittest.main()
