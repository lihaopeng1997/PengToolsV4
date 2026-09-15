# -*- coding: utf-8 -*-
"""DC-01 增量模型协议的本地测试。

所有响应均来自内存 transport；测试不启端口、不连接模型或数据库。分片
刻意跨越 UTF-8、SSE、JSON 和工具参数边界，确保生产解析器确实是增量的。
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

from tools.data_center.contracts import AgentTurnRequest, CancellationToken, ModelDelta, RunContext
from tools.data_center.model_adapter import ModelAdapter, ModelCancelled, ModelTransportError
from tools.data_center.streaming import (
    ModelStreamParser,
    SSEEvent,
    SSEParser,
    ToolArgumentsError,
)


@dataclass(frozen=True)
class SseFixture:
    """一组可重复的内存响应及其协议期望。"""

    name: str
    wire: bytes
    chunks: tuple[bytes, ...]
    expected_event_types: tuple[str, ...]


class IncrementalDecoder(Protocol):
    """供最终 streaming contract 对接的最小测试协议。"""

    def feed(self, chunk: bytes) -> Iterable[object]: ...

    def finish(self) -> Iterable[object]: ...


def _frame(payload: str) -> bytes:
    return f"data: {payload}\n\n".encode("utf-8")


def _split_at(data: bytes, cuts: tuple[int, ...]) -> tuple[bytes, ...]:
    valid_cuts = sorted({cut for cut in cuts if 0 < cut < len(data)})
    points = (0,) + tuple(valid_cuts) + (len(data),)
    return tuple(data[left:right] for left, right in zip(points, points[1:]) if left < right)


def _boundaries(chunks: Iterable[bytes]) -> set[int]:
    """返回每个内存分片结束位置，便于断言是否真的跨越了协议单元。"""

    result: set[int] = set()
    position = 0
    for chunk in chunks:
        position += len(chunk)
        result.add(position)
    return result


def make_stream_fixtures() -> tuple[SseFixture, ...]:
    """构造不访问网络的协议边界样本。"""

    reasoning = _frame(json.dumps(
        {"choices": [{"delta": {"reasoning_content": "正在读取订单结构"}}]},
        ensure_ascii=False,
    ))
    text = _frame(json.dumps(
        {"choices": [{"delta": {"content": "订单共有 12 条"}}]},
        ensure_ascii=False,
    ))
    usage = _frame(json.dumps(
        {"choices": [], "usage": {"prompt_tokens": 18, "completion_tokens": 7}},
        ensure_ascii=False,
    ))
    done = _frame("[DONE]")
    wire = reasoning + b": heartbeat\n\n" + text + usage + done

    tool_payload = {
        "id": "call-demo-1",
        "type": "function",
        "function": {
            "name": "query_readonly",
            "arguments": json.dumps(
                {"sql": "SELECT * FROM orders WHERE city = '北京'", "row_limit": 20},
                ensure_ascii=False,
            ),
        },
    }
    tool_json = json.dumps(
        {"choices": [{"delta": {"tool_calls": [tool_payload]}}]},
        ensure_ascii=False,
    ).encode("utf-8")
    tool_wire = _frame(tool_json.decode("utf-8")) + _frame("[DONE]")

    # 分片特意落在 UTF-8 中文字节、SSE 行边界和工具 arguments JSON 内部。
    city_bytes = "北京".encode("utf-8")
    city_start = tool_wire.index(city_bytes)
    tool_cuts = (11, 29, city_start + 1, city_start + len(city_bytes) - 1, 167)
    return (
        SseFixture(
            name="reasoning_text_usage_done_with_heartbeat",
            wire=wire,
            chunks=_split_at(wire, (7, 19, 31, 47, 63, 79)),
            expected_event_types=("reasoning_delta", "text_delta", "usage", "turn_done"),
        ),
        SseFixture(
            name="tool_arguments_split_until_complete",
            wire=tool_wire,
            chunks=_split_at(tool_wire, tool_cuts),
            expected_event_types=("tool_call_delta", "turn_done"),
        ),
    )


def feed_chunks(decoder: IncrementalDecoder, chunks: Iterable[bytes]) -> list[object]:
    """将内存分片逐个交给 decoder，结束时再 flush 半帧缓存。"""

    events: list[object] = []
    for chunk in chunks:
        events.extend(decoder.feed(chunk))
    events.extend(decoder.finish())
    return events


class _RecordingDecoder:
    """仅验证夹具交付顺序；不模拟生产解析逻辑。"""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def feed(self, chunk: bytes) -> Iterable[object]:
        self.chunks.append(chunk)
        return ()

    def finish(self) -> Iterable[object]:
        return ()


class DataCenterStreamingFixtureTests(unittest.TestCase):
    def test_fixture_delivery_preserves_arbitrary_byte_chunks(self) -> None:
        for fixture in make_stream_fixtures():
            decoder = _RecordingDecoder()
            feed_chunks(decoder, fixture.chunks)
            self.assertEqual(b"".join(decoder.chunks), fixture.wire, fixture.name)
            self.assertTrue(all(isinstance(chunk, bytes) for chunk in decoder.chunks))

    def test_utf8_and_tool_json_boundaries_are_actually_split(self) -> None:
        fixtures = {item.name: item for item in make_stream_fixtures()}
        text_fixture = fixtures["reasoning_text_usage_done_with_heartbeat"]
        tool_fixture = fixtures["tool_arguments_split_until_complete"]

        self.assertIn("北京".encode("utf-8"), tool_fixture.wire)
        city_start = tool_fixture.wire.index("北京".encode("utf-8"))
        city_end = city_start + len("北京".encode("utf-8"))
        self.assertTrue(any(
            city_start < boundary < city_end
            for boundary in _boundaries(tool_fixture.chunks)
        ), "工具参数样本应包含跨 UTF-8 字符的分片")
        self.assertIn(b": heartbeat", text_fixture.wire)
        self.assertIn(b"\"usage\"", text_fixture.wire)
        self.assertEqual(text_fixture.expected_event_types[-1], "turn_done")

    def test_tool_call_fixture_requires_a_complete_arguments_buffer(self) -> None:
        fixture = next(item for item in make_stream_fixtures() if item.name == "tool_arguments_split_until_complete")
        data_line = next(line for line in fixture.wire.splitlines() if line.startswith(b"data: {"))
        outer = json.loads(data_line[len(b"data: "):].decode("utf-8"))
        argument_value = outer["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
        encoded_argument = json.dumps(argument_value, ensure_ascii=False).encode("utf-8")
        argument_start = fixture.wire.index(encoded_argument)
        argument_end = argument_start + len(encoded_argument)
        self.assertTrue(any(
            argument_start < boundary < argument_end
            for boundary in _boundaries(fixture.chunks)
        ), "tool arguments 应在 SSE 分片中跨越多个 chunk")
        # 这是协议验收用的事实：在完整 arguments 之前，生产层不得调 executor。
        tool_name = outer["choices"][0]["delta"]["tool_calls"][0]["function"]["name"]
        self.assertEqual(tool_name, "query_readonly")
        self.assertIn("北京", argument_value)

    def test_sse_parser_decodes_utf8_and_heartbeat_incrementally(self) -> None:
        fixture = make_stream_fixtures()[0]
        parser = SSEParser()
        events: list[SSEEvent] = []
        for chunk in fixture.chunks:
            events.extend(parser.feed(chunk))
        events.extend(parser.finish())

        self.assertEqual(
            [json.loads(event.data)["choices"][0]["delta"].get("content")
             or json.loads(event.data)["choices"][0]["delta"].get("reasoning_content")
             for event in events[:2]],
            ["正在读取订单结构", "订单共有 12 条"],
        )
        self.assertEqual(json.loads(events[2].data)["usage"]["prompt_tokens"], 18)
        self.assertEqual(events[3].data, "[DONE]")

    def test_model_parser_exposes_explicit_channels_and_defers_tool_finalization(self) -> None:
        parser = ModelStreamParser()
        events: list[dict] = []
        for chunk in make_stream_fixtures()[0].chunks:
            events.extend(parser.feed(chunk))
        events.extend(parser.finish())

        self.assertEqual(
            [event["kind"] for event in events],
            ["reasoning_delta", "text_delta", "usage", "turn_done"],
        )
        self.assertEqual(events[0]["text"], "正在读取订单结构")
        self.assertEqual(events[1]["text"], "订单共有 12 条")
        self.assertEqual(parser.usage["completion_tokens"], 7)

        # A JSON argument split over two separate SSE events is never exposed
        # as a complete call until its second fragment has arrived.
        first = _frame(json.dumps({
            "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call-1",
                "type": "function",
                "function": {"name": "query_readonly", "arguments": '{"sql":'},
            }]}}],
        }, ensure_ascii=False))
        second = _frame(json.dumps({
            "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"arguments": '"SELECT 1"}'},
            }]}}],
        }, ensure_ascii=False))
        split = ModelStreamParser()
        split.feed(first)
        with self.assertRaises(ToolArgumentsError):
            split.finalize_tool_calls()
        split.feed(second)
        self.assertEqual(split.finalize_tool_calls()[0]["arguments"], {"sql": "SELECT 1"})

    def test_multiple_tool_indexes_usage_and_done_are_preserved(self) -> None:
        payloads = [
            {"choices": [{"delta": {"tool_calls": [{
                "index": 1, "id": "b", "function": {"name": "describe_object", "arguments": "{"},
            }, {"index": 0, "id": "a", "function": {"name": "search_objects", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 1, "function": {"arguments": "}"},
            }]}}], "usage": {"total_tokens": 9}},
        ]
        wire = b"".join(_frame(json.dumps(payload, ensure_ascii=False)) for payload in payloads) + _frame("[DONE]")
        parser = ModelStreamParser()
        events: list[dict] = []
        for chunk in _split_at(wire, (3, 17, 31, 47, 61, 79)):
            events.extend(parser.feed(chunk))
        events.extend(parser.finish())
        calls = parser.finalize_tool_calls()
        self.assertEqual([call["call_id"] for call in calls], ["b", "a"])
        self.assertEqual(calls[0]["arguments"], {})
        self.assertEqual(calls[1]["arguments"], {})
        self.assertEqual(parser.usage["total_tokens"], 9)
        self.assertEqual(events[-1]["kind"], "turn_done")


def _request() -> AgentTurnRequest:
    return AgentTurnRequest(
        context=RunContext(
            run_id="run-1",
            tab_id="tab-1",
            model_config_id="model-config-1",
            connection_id="connection-1",
            database="demo",
        ),
        system_prompt="只执行只读查询。",
        messages=({"role": "user", "content": "订单数量？"},),
        tools=({"type": "function", "function": {"name": "query_readonly"}},),
        turn_index=0,
        max_output_tokens=128,
    )


class _MemoryTransport:
    def __init__(self, chunks: Iterable[bytes | str | Mapping[str, object]]) -> None:
        self.chunks = tuple(chunks)
        self.calls = 0

    def stream(self, body: dict, *, cancellation: CancellationToken):
        self.calls += 1
        return iter(self.chunks)


class _ClosingTransport:
    def __init__(self, first: bytes) -> None:
        self.first = first
        self.closed = False
        self.calls = 0

    def stream(self, body: dict, *, cancellation: CancellationToken):
        self.calls += 1
        owner = self

        class Source:
            def __iter__(self):
                yield owner.first
                yield _frame(json.dumps({"choices": [{"delta": {"content": "迟到"}}]}))

            def close(self):
                owner.closed = True

        return Source()


class _Read1Response:
    def __init__(self, chunks: Iterable[bytes], owner: "_Read1Transport") -> None:
        self.chunks = tuple(chunks)
        self.owner = owner
        self.read1_calls = 0
        self.read_calls = 0
        self.closed = False

    def read1(self, size: int) -> bytes:
        self.read1_calls += 1
        if self.read1_calls == 2:
            self.owner.second_read_observed_delta = self.owner.delta_seen
        if self.chunks:
            return self.chunks[self.read1_calls - 1] if self.read1_calls <= len(self.chunks) else b""
        return b""

    def read(self, size: int) -> bytes:
        self.read_calls += 1
        raise AssertionError("streaming transport should prefer read1 when available")

    def close(self) -> None:
        self.closed = True


class _Read1Transport:
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self.chunks = tuple(chunks)
        self.delta_seen = False
        self.second_read_observed_delta = False
        self.response: _Read1Response | None = None

    def stream(self, body: dict, *, cancellation: CancellationToken):
        self.response = _Read1Response(self.chunks, self)
        return self.response


class _FlakyTransport:
    def __init__(self, response: Iterable[bytes], failures: int) -> None:
        self.response = tuple(response)
        self.failures = failures
        self.calls = 0

    def stream(self, body: dict, *, cancellation: CancellationToken):
        self.calls += 1
        if self.calls <= self.failures:
            raise ModelTransportError("temporary", status_code=503)
        return iter(self.response)


class DataCenterModelAdapterTests(unittest.TestCase):
    def test_adapter_delivers_real_deltas_and_builds_contract_turn(self) -> None:
        fixture = make_stream_fixtures()[0]
        transport = _MemoryTransport(fixture.chunks)
        deltas: list[ModelDelta] = []
        turn = ModelAdapter({"model": "demo-model"}, transport=transport).complete_turn(
            _request(), cancellation=CancellationToken(), on_delta=deltas.append,
        )
        self.assertEqual(transport.calls, 1)
        self.assertEqual(turn.text, "订单共有 12 条")
        self.assertEqual(turn.tool_calls, ())
        self.assertEqual(turn.usage["prompt_tokens"], 18)
        self.assertEqual([delta.kind for delta in deltas], [
            "reasoning_delta", "text_delta", "usage", "turn_done",
        ])
        self.assertTrue(all(isinstance(delta, ModelDelta) for delta in deltas))

    def test_adapter_does_not_execute_or_return_partial_tool_arguments(self) -> None:
        first_payload = {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-q",
                        "function": {
                            "name": "query_readonly",
                            "arguments": '{"sql":',
                        },
                    }],
                },
            }],
        }
        second_payload = {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                    "function": {"arguments": '"SELECT 1"}'},
                }],
                },
            }],
        }
        first = _frame(json.dumps(first_payload, ensure_ascii=False))
        second = _frame(json.dumps(second_payload, ensure_ascii=False)) + _frame("[DONE]")
        transport = _MemoryTransport(_split_at(first + second, (9, 23, 41, 67, 101)))
        turn = ModelAdapter({"model": "demo-model"}, transport=transport).complete_turn(
            _request(), cancellation=CancellationToken(), on_delta=lambda _delta: None,
        )
        self.assertEqual(len(turn.tool_calls), 1)
        self.assertEqual(turn.tool_calls[0].call_id, "call-q")
        self.assertEqual(turn.tool_calls[0].arguments, {"sql": "SELECT 1"})

    def test_non_stream_response_is_explicitly_marked(self) -> None:
        response = {
            "model": "demo-model",
            "choices": [{"message": {"content": "直接返回"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 3},
        }
        deltas: list[ModelDelta] = []
        turn = ModelAdapter({"model": "demo-model"}, transport=_MemoryTransport((response,))).complete_turn(
            _request(), cancellation=CancellationToken(), on_delta=deltas.append,
        )
        self.assertEqual(turn.text, "直接返回")
        self.assertEqual(deltas[0].kind, "non_streaming")
        self.assertEqual(deltas[0].usage["streaming"], False)

    def test_cancellation_closes_source_and_emits_no_late_delta(self) -> None:
        cancellation = CancellationToken()
        transport = _ClosingTransport(_frame(json.dumps({
            "choices": [{"delta": {"content": "首片"}}],
        }, ensure_ascii=False)))
        deltas: list[ModelDelta] = []

        def receive(delta: ModelDelta) -> None:
            deltas.append(delta)
            if delta.kind == "text_delta":
                cancellation.cancel("user_stop")

        with self.assertRaises(ModelCancelled) as caught:
            ModelAdapter({"model": "demo-model"}, transport=transport).complete_turn(
                _request(), cancellation=cancellation, on_delta=receive,
            )
        self.assertEqual(transport.calls, 1)
        self.assertTrue(transport.closed)
        self.assertEqual([delta.text for delta in deltas if delta.kind == "text_delta"], ["首片"])
        self.assertEqual(caught.exception.partial_turn.text, "首片")

    def test_response_read1_keeps_small_chunks_visible_and_closes_response(self) -> None:
        chunks = (
            _frame(json.dumps({"choices": [{"delta": {"content": "首片"}}]}, ensure_ascii=False)),
            _frame(json.dumps({"choices": [{"delta": {"content": "次片"}}]}, ensure_ascii=False)),
            _frame("[DONE]"),
        )
        transport = _Read1Transport(chunks)
        deltas: list[ModelDelta] = []

        def receive(delta: ModelDelta) -> None:
            deltas.append(delta)
            if delta.kind == "text_delta":
                transport.delta_seen = True

        turn = ModelAdapter({"model": "demo-model"}, transport=transport).complete_turn(
            _request(), cancellation=CancellationToken(), on_delta=receive,
        )
        assert transport.response is not None
        self.assertEqual(turn.text, "首片次片")
        self.assertGreaterEqual(transport.response.read1_calls, 3)
        self.assertEqual(transport.response.read_calls, 0)
        self.assertTrue(transport.second_read_observed_delta)
        self.assertTrue(transport.response.closed)

    def test_retryable_gateway_failure_retries_before_any_stream_content(self) -> None:
        response = (
            _frame(json.dumps({"choices": [{"delta": {"content": "重试成功"}}]}, ensure_ascii=False)),
            _frame("[DONE]"),
        )
        transport = _FlakyTransport(response, failures=2)
        turn = ModelAdapter({"model": "demo-model"}, transport=transport).complete_turn(
            _request(), cancellation=CancellationToken(), on_delta=lambda _delta: None,
        )
        self.assertEqual(transport.calls, 3)
        self.assertEqual(turn.text, "重试成功")

    def test_partial_stream_failure_is_not_retried(self) -> None:
        class PartialFailure:
            def __init__(self) -> None:
                self.calls = 0

            def stream(self, body: dict, *, cancellation: CancellationToken):
                self.calls += 1

                def source():
                    yield _frame(json.dumps({"choices": [{"delta": {"content": "已有"}}]}, ensure_ascii=False))
                    raise ModelTransportError("temporary", status_code=503)

                return source()

        transport = PartialFailure()
        with self.assertRaises(ModelTransportError):
            ModelAdapter({"model": "demo-model"}, transport=transport).complete_turn(
                _request(), cancellation=CancellationToken(), on_delta=lambda _delta: None,
            )
        self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
