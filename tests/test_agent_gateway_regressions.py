"""Offline regressions for the actual Agent host/transport boundaries."""
from __future__ import annotations

from concurrent.futures import Future
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import agent_diag
from tools.agent_core import AgentStatus, ToolResult
from tools.agent_core import AgentTurnRequest, CancellationToken
from tools.agent_core.model_adapter import ModelAdapter, ModelDeadlineExceeded
from tools.data_center.contracts import RunContext
from tools.agent_runtime import run_agent_loop
from tools.data_center.host_runtime import DataCenterAgentRuntime
from tools.data_center.tool_registry import QueryOwnerTracker


class Transport:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.requests = []

    def stream(self, body, **_kwargs):
        self.requests.append(body)
        answer = next(self.answers)
        return {"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]}


def config(capability):
    return {"id": "fixture", "enabled": True, "provider": "intranet",
            "base_url": "http://127.0.0.1:1/v1", "model": "fixture",
            "agent_capability": capability, "max_tokens": 1024}


class AgentGatewayRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log_patch = patch.object(agent_diag, "_LOG_DIR", self.temp.name)
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def test_read_result_redaction_preserves_structure_and_remaining_code(self):
        root = Path(self.temp.name)
        # Synthetic values only. Escapes/newlines must survive the projection.
        (root / "example.py").write_text(
            'token = synthetic_value\nanswer = 42\npassword = synthetic_password', encoding="utf-8")
        transport = Transport([
            '<tool_call><function=read_file><parameter=path>example.py</parameter></function></tool_call>',
            'read complete',
        ])
        final, messages, calls = run_agent_loop(
            "read example", str(root), config("unknown"), [], [], model_transport=transport)
        self.assertEqual(final, "read complete")
        observation = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
        self.assertTrue(observation["ok"])
        self.assertTrue(observation["data"]["ok"])
        self.assertIn("answer = 42", observation["data"]["data"])
        serialized = json.dumps(observation)
        self.assertNotIn("synthetic_value", serialized)
        self.assertNotIn("synthetic_password", serialized)
        self.assertEqual(len(calls), 1)
        self.assertIn("answer = 42", json.dumps(transport.requests[1]))

    def _database_roundtrip(self, capability, *, json_envelope=False):
        context = RunContext(run_id="fixture-run", tab_id="fixture-tab", model_config_id="fixture",
                             connection_id="fixture-db", database="DB1", schema_allowlist=("APP",),
                             profile_revision="1", intent="query")
        seen = []

        class Metadata:
            def execute(self, call, **_kwargs):
                seen.append(call.name)
                data = ({"objects": [{"object_id": "obj-1", "name": "ITEMS", "schema": "APP", "kind": "table"}]}
                        if call.name == "search_objects" else
                        {"object_id": "obj-1", "name": "ITEMS", "schema": "APP", "fields": [{"name": "ID", "type": "NUMBER"}]})
                return ToolResult(ok=True, data=data, query_id=call.call_id, evidence_id="fixture-metadata")

        class Query:
            host_only = True
            verified_readonly = True

            def __call__(self, call, *, context, **_kwargs):
                seen.append(call.name)
                future = Future()
                future.query_id, future.session_id, future.generation = call.call_id, context.tab_id, 1
                future.set_result(None)
                runtime.query_owner_tracker.register_query_owner(
                    context, call.call_id, session_id=context.tab_id, generation=1, future=future)
                return ToolResult(ok=True, data={"columns": [{"name": "TOTAL"}], "rows": [[12]]},
                                  query_id=call.call_id, evidence_id="fixture-query")

        calls = [("search_objects", {"keyword": "ITEMS"}), ("describe_object", {"object_id": "obj-1"}),
                 ("query_readonly", {"sql": "SELECT COUNT(*) AS TOTAL FROM APP.ITEMS"})]
        if capability == "strict_json" or json_envelope:
            answers = [json.dumps({"tool_calls": [{"id": f"call-{i}", "name": name, "arguments": args}]})
                       for i, (name, args) in enumerate(calls)]
            answers.append(json.dumps({"tool_calls": [], "final_answer": "12 rows"}))
        else:
            answers = ["<tool_call><function=" + name + ">" + "".join(
                f"<parameter={key}>{value}</parameter>" for key, value in args.items()) +
                "</function></tool_call>" for name, args in calls]
            answers.append("12 rows")
        transport = Transport(answers)
        runtime = DataCenterAgentRuntime(context, provider="oracle", model_config_id="fixture",
                                        model_config=config(capability), transport=transport,
                                        metadata_host=Metadata(), query_tool=Query())
        self.addCleanup(runtime.close)
        result = runtime.run("count items")
        self.assertEqual(seen, [name for name, _args in calls])
        self.assertEqual(result.status, AgentStatus.COMPLETED, result.error)
        self.assertEqual(result.final_text, "12 rows")
        self.assertTrue(next(e for e in result.events if e.event_type == "final").payload["verified"])
        system = transport.requests[0]["messages"][0]["content"]
        self.assertIn('"object_id"', system)
        self.assertIn('"sql"', system)
        self.assertIn('"parameters"', system)
        self.assertNotIn("tools", transport.requests[0])
        self.assertIn('"tool_observations"', json.dumps(transport.requests[-1], ensure_ascii=False).replace('\\"', '"'))
        return transport

    def test_database_text_protocol_has_parameter_schemas_and_executes_full_query(self):
        self._database_roundtrip("unknown")

    def test_unknown_gateway_explicit_json_calls_execute_and_final_answer_is_plain_text(self):
        self._database_roundtrip("unknown", json_envelope=True)

    def test_database_strict_json_executes_full_query(self):
        transport = self._database_roundtrip("strict_json")
        self.assertEqual(transport.requests[0]["response_format"], {"type": "json_object"})

    def test_diagnostics_preserve_stages_without_capturing_source_or_query(self):
        self._database_roundtrip("unknown")
        records = [json.loads(line) for line in Path(agent_diag.log_file_path()).read_text().splitlines()]
        events = [row["event"] for row in records]
        for event in ("run_started", "turn_started", "tool_started", "tool_result", "model_request", "model_turn_end", "final"):
            self.assertIn(event, events)
        text = json.dumps(records)
        for private in ("SELECT COUNT", "APP.ITEMS", "count items", "12 rows", "127.0.0.1", "fixture-run"):
            self.assertNotIn(private, text)
        ids = {row["run_id"] for row in records if "run_id" in row}
        self.assertEqual(len(ids), 1)

    def test_logger_rotates_and_discards_unrecognized_payload_fields(self):
        with patch.object(agent_diag, "MAX_LOG_BYTES", 800):
            for _ in range(30):
                agent_diag.log_agent_event('tool_result', tool='read_file', ok=True,
                                           sql='sensitive-sql', content='sensitive-content',
                                           path='sensitive-path', schema='sensitive-schema',
                                           error='sensitive-error', code='sensitive-code')
        paths = sorted(Path(self.temp.name).glob('agent_diag.log*'))
        self.assertEqual(len(paths), 3)
        for path in paths:
            self.assertLessEqual(path.stat().st_size, 800)
            self.assertNotIn('sensitive-', path.read_text())
            for line in path.read_text().splitlines():
                self.assertEqual(json.loads(line)['event'], 'tool_result')

    def test_policy_rejection_cannot_retire_an_existing_physical_worker(self):
        tracker = QueryOwnerTracker()
        context = RunContext(run_id='owned', tab_id='t', model_config_id='fixture')
        tracker.expect_query(context, 'query')
        future = Future()
        future.query_id, future.session_id, future.generation = 'query', 't', 1
        tracker.register_query_owner(context, 'query', session_id='t', generation=1, future=future)
        tracker.reject_before_dispatch(context, 'query')
        self.assertEqual(tracker.state(context), 'pending')
        future.set_result(None)
        self.assertEqual(tracker.state(context), 'idle')

    def test_model_stalled_after_finish_frame_records_timeout_and_stream_state(self):
        stopped = threading.Event()

        class StalledTransport:
            def stream(self, _body, *, cancellation, **_kwargs):
                yield b'data: {"choices":[{"delta":{"content":"synthetic private answer"},"finish_reason":"stop"}]}\n\n'
                while not cancellation.is_cancelled():
                    time.sleep(0.005)
                stopped.set()

        adapter = ModelAdapter(config('unknown'), transport=StalledTransport(), deadline_seconds=0.12)
        request = AgentTurnRequest(context=RunContext(run_id='timeout-run', tab_id='t', model_config_id='fixture'),
                                   messages=({'role': 'user', 'content': 'synthetic private prompt'},),
                                   system_prompt='', tools=(), turn_index=0)
        with self.assertRaises(ModelDeadlineExceeded):
            adapter.complete_turn(request, cancellation=CancellationToken(), on_delta=lambda _delta: None)
        self.assertTrue(stopped.wait(1))
        records = [json.loads(line) for line in Path(agent_diag.log_file_path()).read_text().splitlines()]
        end = next(row for row in records if row['event'] == 'model_stream_end')
        self.assertEqual(end['finish_reason'], 'stop')
        self.assertFalse(end['done'])
        self.assertEqual(records[-1]['error_type'], 'ModelDeadlineExceeded')
        self.assertNotIn('synthetic private', json.dumps(records))


if __name__ == "__main__":
    unittest.main()
