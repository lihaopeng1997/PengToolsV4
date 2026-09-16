from __future__ import annotations

import json
import socket
import time
import unittest
from unittest.mock import patch

from tools.data_center.contracts import AgentTurnRequest, CancellationToken, ModelDelta, RunContext
from tools.data_center.host_model_adapter import AgentModelHostAdapter
from tools.data_center.model_adapter import (
    ModelCancelled,
    ModelDeadlineExceeded,
    ModelProtocolError,
    UrlLibStreamingTransport,
)
from tools.data_center.model_config import AgentModelCapability, AgentModelConfigError


SECRET = "MODEL_TOKEN_SHOULD_STAY_PRIVATE"
ENDPOINT = "http://10.128.1.20:8000/v1"


def _config(config_id: str = "cfg-selected", **extra):
    value = {
        "id": config_id,
        "enabled": True,
        "base_url": ENDPOINT,
        "model": "qwen-agent",
        "token": SECRET,
        "ssl_verify": True,
        "timeout_seconds": 120,
        "include_usage": True,
    }
    value.update(extra)
    return value


def _request(config_id: str = "cfg-selected", tools=()):
    return AgentTurnRequest(
        context=RunContext(
            run_id="run-1",
            tab_id="tab-1",
            model_config_id=config_id,
            connection_id="connection-1",
            database="demo",
        ),
        system_prompt="只执行只读查询。",
        messages=({"role": "user", "content": "订单数量？"},),
        tools=tuple(tools),
        turn_index=0,
        max_output_tokens=128,
    )


def _frame(payload: object) -> bytes:
    value = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {value}\n\n".encode("utf-8")


class _TextTransport:
    def __init__(self, *chunks: bytes):
        self.chunks = chunks
        self.calls = 0
        self.bodies = []

    def stream(self, body, *, cancellation):
        self.calls += 1
        self.bodies.append(dict(body))
        return iter(self.chunks)


class _Read1Response:
    def __init__(self, chunks, owner):
        self.chunks = tuple(chunks)
        self.owner = owner
        self.read1_calls = 0
        self.read_calls = 0
        self.closed = False

    def read1(self, size):
        self.read1_calls += 1
        if self.read1_calls == 2:
            self.owner.delta_was_seen_before_second_read = self.owner.delta_seen
        index = self.read1_calls - 1
        return self.chunks[index] if index < len(self.chunks) else b""

    def read(self, size):
        self.read_calls += 1
        raise AssertionError("the Agent transport must prefer read1")

    def close(self):
        self.closed = True


class _Read1Opener:
    def __init__(self, chunks):
        self.delta_seen = False
        self.delta_was_seen_before_second_read = False
        self.response = _Read1Response(chunks, self)

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


class _NeverOpenOpener:
    def __init__(self) -> None:
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        raise AssertionError("cancelled requests must not call opener.open")


class _TimeoutResponse:
    def __init__(self) -> None:
        self.read_calls = 0
        self.closed = False

    def read1(self, size):
        self.read_calls += 1
        raise socket.timeout("heartbeat timeout")

    def close(self):
        self.closed = True


class AgentModelHostAdapterTests(unittest.TestCase):
    def test_native_tools_sends_tools_and_selected_model_name(self) -> None:
        transport = _TextTransport(
            _frame({"model": "qwen-agent", "choices": [{"delta": {"content": "12 条"}}]}),
            _frame("[DONE]"),
        )
        adapter = AgentModelHostAdapter(
            "cfg-selected",
            config=_config(agent_capability="native_tools"),
            transport=transport,
        )
        turn = adapter.complete_turn(
            _request(tools=({"type": "function", "function": {"name": "query_readonly"}},)),
            cancellation=CancellationToken(),
            on_delta=lambda _delta: None,
        )

        body = transport.bodies[0]
        self.assertEqual(body["model"], "qwen-agent")
        self.assertNotEqual(body["model"], "cfg-selected")
        self.assertIn("tools", body)
        self.assertEqual(body["stream"], True)
        self.assertEqual(turn.text, "12 条")
        self.assertTrue(adapter.snapshot.tools_enabled)

    def test_text_only_and_unknown_omit_tools(self) -> None:
        for capability in ("text_only", "unknown"):
            with self.subTest(capability=capability):
                transport = _TextTransport(_frame({"choices": [{"delta": {"content": "草稿"}}]}), _frame("[DONE]"))
                adapter = AgentModelHostAdapter(
                    "cfg-selected",
                    config=_config(agent_capability=capability),
                    transport=transport,
                )
                adapter.complete_turn(
                    _request(tools=({"type": "function", "function": {"name": "query_readonly"}},)),
                    cancellation=CancellationToken(),
                    on_delta=lambda _delta: None,
                )
                body = transport.bodies[0]
                self.assertNotIn("tools", body)
                self.assertNotIn("response_format", body)
                self.assertEqual(adapter.effective_capability, AgentModelCapability.TEXT_ONLY)

    def test_snapshot_reasoning_fields_reach_the_stream_parser(self) -> None:
        transport = _TextTransport(
            _frame({"choices": [{"delta": {"reasoning": "配置允许的摘要"}}]}),
            _frame("[DONE]"),
        )
        adapter = AgentModelHostAdapter(
            "cfg-selected",
            config=_config(agent_capability="text_only", agent_reasoning_fields=["reasoning"]),
            transport=transport,
        )
        deltas: list[ModelDelta] = []
        adapter.complete_turn(_request(), cancellation=CancellationToken(), on_delta=deltas.append)

        self.assertEqual([delta.text for delta in deltas if delta.kind == "reasoning_delta"], ["配置允许的摘要"])

    def test_strict_json_is_structured_but_never_executes_tool_calls(self) -> None:
        transport = _TextTransport(
            _frame({
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "call-1",
                    "function": {"name": "query_readonly", "arguments": "{}"},
                }]}}],
            }),
            _frame("[DONE]"),
        )
        adapter = AgentModelHostAdapter(
            "cfg-selected",
            config=_config(agent_capability="strict_json"),
            transport=transport,
        )
        with self.assertRaises(ModelProtocolError):
            adapter.complete_turn(
                _request(tools=({"type": "function", "function": {"name": "query_readonly"}},)),
                cancellation=CancellationToken(),
                on_delta=lambda _delta: None,
            )
        body = transport.bodies[0]
        self.assertNotIn("tools", body)
        self.assertEqual(body["response_format"]["type"], "json_object")

    def test_context_mismatch_is_rejected_before_transport(self) -> None:
        transport = _TextTransport(_frame("[DONE]"))
        adapter = AgentModelHostAdapter(
            "cfg-selected",
            config=_config(agent_capability="native_tools"),
            transport=transport,
        )
        with self.assertRaises(AgentModelConfigError):
            adapter.complete_turn(
                _request(config_id="another-config"),
                cancellation=CancellationToken(),
                on_delta=lambda _delta: None,
            )
        self.assertEqual(transport.calls, 0)

    def test_default_transport_uses_read1_and_emits_before_next_read(self) -> None:
        chunks = (
            _frame({"choices": [{"delta": {"content": "首片"}}]}),
            _frame({"choices": [{"delta": {"content": "次片"}}]}),
            _frame("[DONE]"),
        )
        opener = _Read1Opener(chunks)
        config = _config(agent_capability="text_only")
        deltas: list[ModelDelta] = []

        with patch("tools.intranet_llm._direct_opener", return_value=opener), \
                patch("tools.intranet_llm.build_headers", return_value={}), \
                patch("tools.intranet_llm.resolve_private_host"):
            adapter = AgentModelHostAdapter(
                "cfg-selected",
                config=config,
                transport=UrlLibStreamingTransport(config),
            )
            turn = adapter.complete_turn(
                _request(),
                cancellation=CancellationToken(),
                on_delta=lambda delta: (deltas.append(delta), setattr(opener, "delta_seen", True))[-1],
            )

        self.assertEqual(turn.text, "首片次片")
        self.assertGreaterEqual(opener.response.read1_calls, 3)
        self.assertEqual(opener.response.read_calls, 0)
        self.assertTrue(opener.delta_was_seen_before_second_read)
        self.assertTrue(opener.response.closed)

    def test_cancellation_closes_default_response_without_late_text(self) -> None:
        chunks = (
            _frame({"choices": [{"delta": {"content": "首片"}}]}),
            _frame({"choices": [{"delta": {"content": "迟到"}}]}),
            _frame("[DONE]"),
        )
        opener = _Read1Opener(chunks)
        config = _config(agent_capability="text_only")
        cancellation = CancellationToken()
        deltas: list[ModelDelta] = []

        def receive(delta):
            deltas.append(delta)
            if delta.kind == "text_delta":
                cancellation.cancel("user_stop")

        with patch("tools.intranet_llm._direct_opener", return_value=opener), \
                patch("tools.intranet_llm.build_headers", return_value={}), \
                patch("tools.intranet_llm.resolve_private_host"):
            adapter = AgentModelHostAdapter(
                "cfg-selected",
                config=config,
                transport=UrlLibStreamingTransport(config),
            )
            with self.assertRaises(ModelCancelled):
                adapter.complete_turn(_request(), cancellation=cancellation, on_delta=receive)

        self.assertEqual([item.text for item in deltas if item.kind == "text_delta"], ["首片"])
        self.assertTrue(opener.response.closed)

    def test_public_metadata_repr_and_errors_do_not_expose_secret(self) -> None:
        adapter = AgentModelHostAdapter(
            "cfg-selected",
            config=_config(agent_capability="unknown"),
            transport=_TextTransport(_frame("[DONE]")),
        )
        public = adapter.public_metadata
        self.assertNotIn(SECRET, repr(adapter))
        self.assertNotIn(SECRET, repr(adapter.snapshot))
        self.assertNotIn(SECRET, repr(public))
        self.assertNotIn(ENDPOINT, repr(adapter.snapshot))

        with self.assertRaises(AgentModelConfigError) as caught:
            AgentModelHostAdapter(
                "cfg-selected",
                loader=lambda _selected: (_ for _ in ()).throw(RuntimeError(SECRET)),
                transport=_TextTransport(_frame("[DONE]")),
            )
        self.assertNotIn(SECRET, str(caught.exception))

    def test_cancelled_before_open_does_not_invoke_opener(self) -> None:
        opener = _NeverOpenOpener()
        cancellation = CancellationToken()
        cancellation.cancel("user_stop")
        transport = UrlLibStreamingTransport(_config(agent_capability="text_only"))

        with patch("tools.intranet_llm._direct_opener", return_value=opener), \
                patch("tools.intranet_llm.build_headers", return_value={}), \
                patch("tools.intranet_llm.resolve_private_host"):
            self.assertEqual(
                list(transport.stream({"model": "qwen-agent"}, cancellation=cancellation)),
                [],
            )

        self.assertEqual(opener.calls, 0)

    def test_repeated_socket_timeouts_stop_at_total_deadline(self) -> None:
        opener = type("Opener", (), {})()
        response = _TimeoutResponse()
        opener.open = lambda request, timeout: response
        cancellation = CancellationToken()
        transport = UrlLibStreamingTransport(_config(agent_capability="text_only", timeout_seconds=0.05))

        started = time.monotonic()
        with patch("tools.intranet_llm._direct_opener", return_value=opener), \
                patch("tools.intranet_llm.build_headers", return_value={}), \
                patch("tools.intranet_llm.resolve_private_host"):
            with self.assertRaises(ModelDeadlineExceeded):
                list(transport.stream(
                    {"model": "qwen-agent"},
                    cancellation=cancellation,
                    deadline=time.monotonic() + 0.05,
                ))

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
