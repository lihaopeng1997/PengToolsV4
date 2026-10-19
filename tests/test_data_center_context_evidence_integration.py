"""Fake-runtime coverage for run-local projected-query recovery."""

from __future__ import annotations

from concurrent.futures import Future
from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

from tools.agent_core.context import ContextLimits, ContextPlanner
from tools.data_center.contracts import AgentStatus, AgentTurn, RunContext, ToolCall, ToolResult
from tools.data_center.context_evidence import DataCenterEvidencePlanner
from tools.data_center.host_runtime import DataCenterAgentRuntime


class _FakeModel:
    def __init__(self) -> None:
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
        self.requests.append(request)
        count = len(self.requests)
        if count == 1:
            return AgentTurn(
                tool_calls=(ToolCall("q1-call", "query_readonly", {"sql": "SELECT id FROM synthetic_schema.orders"}),),
                finish_reason="tool_calls",
            )
        if count == 2:
            return AgentTurn(
                tool_calls=(ToolCall("q2-call", "query_readonly", {"sql": "SELECT id FROM synthetic_schema.orders"}),),
                finish_reason="tool_calls",
            )
        return AgentTurn(text="synthetic answer", finish_reason="stop")


class _FakeMetadataHost:
    def execute(self, call, *, context, cancellation, deadline=None):  # noqa: ARG002
        return ToolResult(ok=True, code="OK", data={"objects": []}, query_id=call.call_id, scope=context.as_dict())


class _OwnedFakeQueryTool:
    verified_readonly = True

    def __init__(self, *, large_result: bool = True) -> None:
        self.large_result = large_result
        self.tracker = None
        self.calls = []

    def __call__(self, call, *, context, cancellation):  # noqa: ARG002
        self.calls.append(call)
        future = Future()
        future.query_id = call.call_id
        future.session_id = context.tab_id
        future.generation = 1
        future.set_result(None)
        if not self.tracker.register_query_owner(
            context,
            call.call_id,
            session_id=context.tab_id,
            generation=1,
            future=future,
        ):
            raise AssertionError("synthetic completed Future was not accepted by the real owner tracker")
        if call.call_id == "q1-call" and self.large_result:
            data = {
                "columns": [{"name": "fact", "type": "string"}, {"name": "padding", "type": "string"}],
                "rows": [
                    ["Q1-FACT-ALPHA" if index == 0 else f"Q1-ROW-{index}", "x" * 850]
                    for index in range(8)
                ],
                "row_count": 8,
            }
        else:
            data = {"rows": [{"fact": "Q2-NEWEST"}], "row_count": 1}
        return ToolResult(
            ok=True,
            code="OK",
            data=data,
            query_id=call.call_id,
            scope=context.as_dict(),
            truncated=False,
            limits={"row_limit": 10},
        )


class DataCenterContextEvidenceIntegrationTests(unittest.TestCase):
    _DENIED_TARGETS = (
        "tools.data_center.readonly_lease.ReadOnlyLeaseFactory.connect",
        "tools.data_center.host_metadata.MetadataHost.execute",
        "urllib.request.urlopen",
        "socket.create_connection",
        "socket.socket.connect",
        "tools.data_center.host_runtime.AgentModelHostAdapter",
        "tools.data_center.host_runtime.MetadataHost",
        "tools.data_center.host_runtime.QueryExecutor",
        "tools.data_center.host_runtime.HostNoSQLRouter",
        "tools.data_center.host_model_adapter.load_agent_model_profile",
    )

    def setUp(self) -> None:
        self._stack = ExitStack()
        self._gates = []
        for target in self._DENIED_TARGETS:
            def denied(*args, _target=target, **kwargs):  # noqa: ARG001
                raise AssertionError(f"denied external boundary reached: {_target}")

            self._gates.append(self._stack.enter_context(patch(target, side_effect=denied)))
        self._runtimes: list[DataCenterAgentRuntime] = []

    def tearDown(self) -> None:
        try:
            for runtime in self._runtimes:
                runtime.close()
            for gate in self._gates:
                self.assertEqual(gate.call_count, 0)
        finally:
            self._stack.close()

    @staticmethod
    def _context() -> RunContext:
        return RunContext(
            run_id="context-evidence-run",
            tab_id="context-evidence-tab",
            model_config_id="synthetic-config",
            connection_id="synthetic-connection",
            database="synthetic-db",
            schema_allowlist=("synthetic_schema",),
            profile_revision="synthetic-revision",
            intent="query",
            policy_version="synthetic-policy",
        )

    def _runtime(self, *, model: _FakeModel | None = None, query: _OwnedFakeQueryTool | None = None):
        context = self._context()
        fake_model = model or _FakeModel()
        fake_query = query or _OwnedFakeQueryTool()
        runtime = DataCenterAgentRuntime(
            context,
            provider="mysql",
            mode="mysql",
            model=fake_model,
            metadata_host=_FakeMetadataHost(),
            query_tool=fake_query,
            system_prompt="Synthetic test scope.",
        )
        fake_query.tracker = runtime.query_owner_tracker
        base = ContextPlanner(ContextLimits(9_000, None, output_reserve_tokens=4_096))
        evidence_planner = DataCenterEvidencePlanner(base, runtime.registry, context)
        runtime._evidence_planner = evidence_planner
        runtime.runner.context_planner = evidence_planner
        self._runtimes.append(runtime)
        return runtime, fake_model, fake_query

    def test_real_runtime_projection_recovers_dropped_q1_and_releases_epoch(self) -> None:
        runtime, model, query = self._runtime()

        result = runtime.run("synthetic current question")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(query.calls), 2)
        self.assertEqual(len(model.requests), 3)
        final_messages = model.requests[-1].messages
        self.assertFalse(any(
            call.get("id") == "q1-call"
            for message in final_messages for call in message.get("tool_calls", ())
        ))
        self.assertTrue(any(
            call.get("id") == "q2-call"
            for message in final_messages for call in message.get("tool_calls", ())
        ))
        overlay = next(
            message for message in final_messages
            if message.get("role") == "assistant"
            and "UNTRUSTED PROJECTED QUERY REFERENCE" in message.get("content", "")
        )
        self.assertIn("Q1-FACT-ALPHA", overlay["content"])
        self.assertNotIn("SELECT id", overlay["content"])
        reference = json.loads(overlay["content"].split("\n", 1)[1])
        q1_reference = next(item for item in reference["queries"] if item["call_id"] == "q1-call")
        self.assertNotIn("profile_revision", q1_reference["scope"])
        self.assertEqual(q1_reference["scope"]["schema_allowlist"], ["synthetic_schema"])
        self.assertEqual(reference["run_scope"]["profile_revision"], "synthetic-revision")
        self.assertNotIn("UNTRUSTED PROJECTED", str(result.messages))
        self.assertTrue(any(
            item.get("tool_call_id") == "q1-call" for item in result.messages
        ))
        self.assertTrue(any(
            item.get("tool_call_id") == "q2-call" for item in result.messages
        ))
        self.assertEqual(runtime.registry._evidence_ledger._sessions, {})

    def test_throwing_commit_disables_recovery_without_changing_run_results(self) -> None:
        runtime, model, query = self._runtime()
        with patch.object(runtime.registry, "commit_tool_batch", side_effect=RuntimeError("synthetic ledger fault")):
            result = runtime.run("synthetic current question")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(query.calls), 2)
        self.assertEqual(len(model.requests), 3)
        final_messages = model.requests[-1].messages
        self.assertFalse(any(
            "UNTRUSTED PROJECTED QUERY REFERENCE" in message.get("content", "")
            for message in final_messages
        ))
        self.assertTrue(any(
            item.get("tool_call_id") == "q1-call" for item in result.messages
        ))
        self.assertTrue(any(
            item.get("tool_call_id") == "q2-call" for item in result.messages
        ))
        self.assertEqual(runtime.registry._evidence_ledger._sessions, {})


if __name__ == "__main__":
    unittest.main()
