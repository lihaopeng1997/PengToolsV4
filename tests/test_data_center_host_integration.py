"""DC-03 host-to-read-only-query integration tests.

The tests use the production pure-Python boundaries end to end while keeping
both external sides in memory: a sharded SSE transport stands in for the
selected model and a DB-API shaped connection stands in for the database.
"""

from __future__ import annotations

import json
import threading
import unittest

from tools.data_center.agent import AgentRunner
from tools.data_center.agent_query_tool import AgentQueryTool
from tools.data_center.contracts import AgentStatus, RunContext
from tools.data_center.host_model_adapter import AgentModelHostAdapter
from tools.data_center.query_executor import QueryExecutor
from tools.data_center.readonly_lease import ReadOnlyLeaseFactory, ReadOnlyTarget
from tools.data_center.result_projection import ResultProjector
from tools.data_center.session_manager import SessionManager
from tools.data_center.sql_policy import SQLPolicy
from tools.data_center.tool_registry import DataCenterToolRegistry


QUERY = "SELECT id, api_token, note FROM orders"
CALL_ID = "call-readonly-1"


def _sse(payload: object) -> bytes:
    value = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {value}\n\n".encode("utf-8")


def _shard(*frames: bytes, width: int = 7) -> tuple[bytes, ...]:
    chunks: list[bytes] = []
    for frame in frames:
        chunks.extend(frame[index : index + width] for index in range(0, len(frame), width))
    return tuple(chunks)


class _MemoryConnection:
    """Small DB-API connection whose every operation records its thread."""

    def __init__(self, record) -> None:
        self._record = record
        self._autocommit = True
        self.closed = False
        self.commit_calls = 0
        self.rows = (
            (1, "db-token-1", "订单说明 " + "长文本" * 120),
            (2, "db-token-2", "订单说明 " + "长文本" * 120),
            (3, "db-token-3", "订单说明 " + "长文本" * 120),
        )

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._record("autocommit", value)
        self._autocommit = value

    def cursor(self):
        self._record("cursor", None)
        return _MemoryCursor(self)

    def rollback(self):
        self._record("rollback", None)

    def commit(self):
        self.commit_calls += 1
        self._record("commit", None)
        raise AssertionError("the read-only Agent path must never commit")

    def close(self):
        self._record("close", None)
        self.closed = True


class _MemoryCursor:
    description = (
        ("id", "INTEGER"),
        ("api_token", "VARCHAR"),
        ("note", "VARCHAR"),
    )

    def __init__(self, connection: _MemoryConnection) -> None:
        self.connection = connection
        self.result_rows = ()

    def execute(self, sql, parameters=None):
        self.connection._record("execute", (sql, parameters))
        normalized = str(sql).strip().upper()
        if normalized == "SET TRANSACTION READ ONLY":
            return self
        if normalized == QUERY.upper():
            self.result_rows = self.connection.rows
            return self
        raise AssertionError(f"unexpected SQL reached the in-memory DB-API: {sql}")

    def fetchmany(self, size):
        self.connection._record("fetchmany", size)
        return self.result_rows[:size]

    def close(self):
        self.connection._record("cursor_close", None)


class _ChunkedTransport:
    """Inject one deterministic stream per model turn and capture request bodies."""

    def __init__(self, streams, db_events) -> None:
        self.streams = tuple(streams)
        self._db_events = db_events
        self.calls = 0
        self.bodies: list[dict] = []
        self.first_turn_db_snapshots: list[tuple] = []
        self.second_turn_db_snapshot: tuple = ()

    def stream(self, body, *, cancellation):  # noqa: ARG002
        call_index = self.calls
        self.calls += 1
        self.bodies.append(dict(body))
        if call_index == 1:
            self.second_turn_db_snapshot = tuple(self._db_events())
        if call_index >= len(self.streams):
            raise AssertionError("the scripted model made an unexpected request")
        chunks = self.streams[call_index]

        def produce():
            for chunk in chunks:
                if call_index == 0:
                    # The runner cannot execute a call while the adapter is
                    # still consuming any reasoning, text, or tool fragment.
                    self.first_turn_db_snapshots.append(tuple(self._db_events()))
                yield chunk

        return produce()


class _NeverCalledFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):  # noqa: ARG002
        self.calls += 1
        raise AssertionError("text-only/unknown runs must not create a database lease")


class DataCenterHostIntegrationTests(unittest.TestCase):
    def _native_fixture(self):
        events: list[tuple[str, int, object]] = []
        lock = threading.Lock()

        def record(kind: str, detail: object) -> None:
            with lock:
                events.append((kind, threading.get_ident(), detail))

        connection = _MemoryConnection(record)

        def load_profile(connection_id, profile_revision):
            record("profile", (connection_id, profile_revision))
            return {
                "connection_id": connection_id,
                "provider": "oracle",
                "profile_revision": profile_revision,
                "mode": "oracle",
                "dialect": "oracle",
            }

        def load_secret(profile):  # noqa: ARG001
            record("secret", None)
            return "in-memory-secret"

        def connector(provider, profile, secret, **options):  # noqa: ARG001
            record("connect", {"provider": provider, **options})
            return connection

        lease_factory = ReadOnlyLeaseFactory(
            ReadOnlyTarget("connection-1", "rev-1", "oracle"),
            profile_loader=load_profile,
            secret_loader=load_secret,
            connector=connector,
        )
        manager = SessionManager(lease_factory)
        session = manager.create_session("connection-1", session_id="tab-1", dialect="oracle")
        executor = QueryExecutor(manager, SQLPolicy(), default_timeout=None)
        projector = ResultProjector(
            max_rows=2,
            max_cell_chars=32,
            max_result_bytes=4 * 1024,
            max_cumulative_bytes=4 * 1024,
        )
        registry = DataCenterToolRegistry(
            {"query_readonly": AgentQueryTool(manager, executor)},
            projector=projector,
        )
        context = RunContext(
            run_id="run-dc03",
            tab_id="tab-1",
            model_config_id="cfg-native",
            connection_id="connection-1",
            database="demo",
            profile_revision="rev-1",
        )
        return events, connection, manager, executor, registry, context

    def test_native_tools_stream_runs_one_guarded_query_and_backfills_projected_tool_result(self) -> None:
        events, connection, manager, executor, registry, context = self._native_fixture()
        del manager  # The registry and executor retain the host-owned session.

        first_turn = _shard(
            _sse({
                "model": "fake-native",
                "choices": [{"delta": {"reasoning_content": "先核对范围"}}],
            }),
            _sse({"choices": [{"delta": {"content": "准备只读查询"}}]}),
            _sse({
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": CALL_ID,
                    "function": {
                        "name": "query_readonly",
                        "arguments": '{"sql":"SELECT id, api_token, note FROM orders","row_limit":',
                    },
                }]}}],
            }),
            _sse({
                "choices": [{
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "10}"}}]},
                    "finish_reason": "tool_calls",
                }],
            }),
            _sse("[DONE]"),
        )
        second_turn = _shard(
            _sse({"choices": [{"delta": {"reasoning_summary": "已核验回传边界"}}]}),
            _sse({
                "choices": [{
                    "delta": {"content": "收到 2 条样本，结果已截断"},
                    "finish_reason": "stop",
                }],
            }),
            _sse({"usage": {"completion_tokens": 9}}),
            _sse("[DONE]"),
        )
        transport = _ChunkedTransport((first_turn, second_turn), lambda: [item for item in events])
        host = AgentModelHostAdapter(
            "cfg-native",
            config={
                "id": "cfg-native",
                "enabled": True,
                "base_url": "http://model.invalid/v1",
                "model": "fake-native",
                "agent_capability": "native_tools",
                "include_usage": True,
            },
            transport=transport,
            retry_count=0,
        )
        observed = []
        try:
            result = AgentRunner(
                host,
                registry,
                system_prompt="只执行当前连接上的只读查询",
                on_event=observed.append,
                ).run(context, "抽样查看订单")
        finally:
            executor.shutdown()

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.final_text, "收到 2 条样本，结果已截断")
        self.assertIn("2 条样本", result.final_text)
        self.assertIn("截断", result.final_text)
        self.assertNotIn("共有", result.final_text)
        self.assertNotIn("总数", result.final_text)
        self.assertEqual(transport.calls, 2)
        self.assertIn("tools", transport.bodies[0])
        self.assertTrue(any(
            item["function"]["name"] == "query_readonly"
            for item in transport.bodies[0]["tools"]
        ))

        # No database event exists at any SSE shard boundary.  The first
        # tool is scheduled only after the adapter has produced a complete
        # JSON call and returned the first turn.
        self.assertTrue(transport.first_turn_db_snapshots)
        self.assertTrue(all(snapshot == () for snapshot in transport.first_turn_db_snapshots))
        event_types = [event.event_type for event in observed]
        tool_started = event_types.index("tool_started")
        self.assertLess(event_types.index("model_reasoning_delta"), tool_started)
        self.assertLess(event_types.index("model_text_delta"), tool_started)

        second_messages = transport.bodies[1]["messages"]
        tool_messages = [message for message in second_messages if message.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], CALL_ID)
        projected = json.loads(tool_messages[0]["content"])
        self.assertEqual(projected["query_id"], CALL_ID)
        self.assertTrue(projected["truncated"])
        self.assertEqual(len(projected["data"]["rows"]), 2)
        self.assertEqual(projected["data"]["rows"][0][1], {"type": "redacted"})
        self.assertTrue(projected["data"]["rows"][0][2]["truncated"])
        self.assertLessEqual(len(tool_messages[0]["content"].encode("utf-8")), 4 * 1024)
        self.assertNotIn("db-token-", tool_messages[0]["content"])

        query_events = [item for item in events if item[0] == "execute"]
        self.assertEqual([item[2][0] for item in query_events], ["SET TRANSACTION READ ONLY", QUERY])
        self.assertEqual(len([item for item in query_events if item[2][0] == QUERY]), 1)
        self.assertEqual(connection.commit_calls, 0)
        self.assertTrue(any(item[0] == "rollback" for item in events))
        self.assertTrue(any(item[0] == "close" for item in events))
        worker_threads = {item[1] for item in events}
        self.assertEqual(len(worker_threads), 1)
        self.assertNotIn(threading.get_ident(), worker_threads)
        self.assertEqual(transport.second_turn_db_snapshot, tuple(events))

    def test_unknown_and_text_only_omit_tools_and_do_zero_database_io(self) -> None:
        for capability in ("unknown", "text_only"):
            with self.subTest(capability=capability):
                factory = _NeverCalledFactory()
                manager = SessionManager(factory)
                manager.create_session("connection-1", session_id="tab-1", dialect="oracle")
                executor = QueryExecutor(manager, SQLPolicy(), default_timeout=None)
                registry = DataCenterToolRegistry(
                    {"query_readonly": AgentQueryTool(manager, executor)},
                )
                context = RunContext(
                    run_id=f"run-{capability}",
                    tab_id="tab-1",
                    model_config_id=f"cfg-{capability}",
                    connection_id="connection-1",
                    database="demo",
                )
                transport = _ChunkedTransport(
                    (_shard(
                        _sse({"choices": [{"delta": {"content": "仅文本草稿"}, "finish_reason": "stop"}]}),
                        _sse("[DONE]"),
                    ),),
                    lambda: (),
                )
                host = AgentModelHostAdapter(
                    f"cfg-{capability}",
                    config={
                        "id": f"cfg-{capability}",
                        "enabled": True,
                        "base_url": "http://model.invalid/v1",
                        "model": "fake-text",
                        "agent_capability": capability,
                    },
                    transport=transport,
                    retry_count=0,
                )
                try:
                    result = AgentRunner(host, registry).run(context, "写一段说明")
                finally:
                    executor.shutdown()
                self.assertEqual(result.status, AgentStatus.COMPLETED)
                self.assertEqual(result.final_text, "仅文本草稿")
                self.assertEqual(transport.calls, 1)
                self.assertNotIn("tools", transport.bodies[0])
                self.assertEqual(factory.calls, 0)


if __name__ == "__main__":
    unittest.main()
