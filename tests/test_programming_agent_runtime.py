from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.agent_core import (
    AgentModelCapability,
    AgentModelHostAdapter,
    AgentTurn,
    CancellationToken,
    ModelDelta,
    ToolCall,
)
from tools.agent_runtime import (
    MAX_READ_FILE_CHARS,
    ProgrammingToolRegistry,
    execute_tool,
    run_agent_loop,
)
from tools.data_center.tool_registry import DataCenterToolRegistry


class _SequenceTransport:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def stream(self, body, *, cancellation):
        self.requests.append(dict(body))
        return iter(self.turns.pop(0))


def _frame(payload):
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


def _tool_turn(call_id, name, arguments):
    return [
        _frame({"model": "fake-model", "choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }]}}]}),
        _frame({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]


def _text_turn(text):
    return [
        _frame({"model": "fake-model", "choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ]


def _model_config():
    return {
        "id": "model-test",
        "enabled": True,
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "fake-model",
        "token": "synthetic-test-token",
        "agent_capability": "native_tools",
        "timeout_seconds": 2,
        "max_tokens": 512,
        "include_usage": False,
    }


class _ScriptedAdapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def complete_turn(self, request, *, cancellation, on_delta):
        self.requests.append(request)
        turn = self.turns.pop(0)
        for delta in turn.get("deltas", ()):
            on_delta(delta)
        return turn["turn"]


class ProgrammingAgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, adapter, *, confirm_cb=None, cancellation=None, progress=None):
        return run_agent_loop(
            "分析并按需修改当前项目",
            str(self.root),
            {"id": "model-test"},
            [],
            [],
            confirm_cb=confirm_cb,
            progress_cb=progress,
            model_adapter=adapter,
            cancellation=cancellation,
        )

    def test_sse_read_search_confirmed_edit_uses_bounded_pages_and_native_call_pairs(self):
        old_line = "NEEDLE = " + ("x" * 700) + "\n"
        project = ("# padding\n" * 3500) + old_line + "value = 1\n"
        (self.root / "project.py").write_text(project, encoding="utf-8")
        transport = _SequenceTransport([
            _tool_turn("read-0", "read_file", {"path": "project.py", "start_offset": 0, "char_count": MAX_READ_FILE_CHARS}),
            _tool_turn("read-1", "read_file", {"path": "project.py", "start_offset": MAX_READ_FILE_CHARS, "char_count": MAX_READ_FILE_CHARS}),
            _tool_turn("read-2", "read_file", {"path": "project.py", "start_offset": MAX_READ_FILE_CHARS * 2, "char_count": MAX_READ_FILE_CHARS}),
            _tool_turn("search-3", "search_code", {"pattern": "NEEDLE", "file_pattern": "*.py"}),
            _tool_turn("edit-4", "edit_file", {"path": "project.py", "old_str": old_line, "new_str": "NEEDLE = revised\n"}),
            _text_turn("修改完成。"),
        ])
        adapter = AgentModelHostAdapter("model-test", config=_model_config(), transport=transport)
        confirmations = []
        progress = []
        final, messages, calls = self._run(
            adapter,
            confirm_cb=lambda title, diff: confirmations.append((title, diff)) or True,
            progress=lambda kind, text: progress.append((kind, text)),
        )

        self.assertEqual(final, "修改完成。")
        self.assertEqual((self.root / "project.py").read_text(encoding="utf-8").splitlines()[-2], "NEEDLE = revised")
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(confirmations), 1)
        self.assertIn(old_line.rstrip("\n"), confirmations[0][1])
        self.assertGreater(len(confirmations[0][1]), 500)
        self.assertTrue(all("tools" in request for request in transport.requests))
        self.assertGreater(len(progress), 0)
        self.assertEqual([text for kind, text in progress if kind == "status" and text.startswith("turn_started:")], [
            "turn_started:0", "turn_started:1", "turn_started:2", "turn_started:3", "turn_started:4", "turn_started:5",
        ])
        self.assertEqual([message.get("content") for message in messages if message.get("role") == "assistant" and message.get("content")], ["修改完成。"])
        tool_call_messages = [item for item in messages if item.get("role") == "assistant" and item.get("tool_calls")]
        self.assertEqual(len(tool_call_messages), 5)
        tool_result_ids = [item.get("tool_call_id") for item in messages if item.get("role") == "tool"]
        self.assertEqual(tool_result_ids, ["read-0", "read-1", "read-2", "search-3", "edit-4"])
        first_result = json.loads(messages[2]["content"])
        self.assertTrue(first_result["data"]["truncated"])

    def test_strict_json_envelope_executes_only_after_complete_parse(self):
        (self.root / "strict.py").write_text("value = 7\n", encoding="utf-8")
        tool_envelope = json.dumps({
            "tool_calls": [{
                "id": "strict-read-1",
                "name": "read_file",
                "arguments": {"path": "strict.py"},
            }],
            "final_answer": "",
        })
        final_envelope = json.dumps({"tool_calls": [], "final_answer": "严格协议完成。"})
        transport = _SequenceTransport([_text_turn(tool_envelope), _text_turn(final_envelope)])
        config = {
            **_model_config(),
            "agent_capability": "strict_json",
            "agent_strict_tool_envelope": True,
        }
        adapter = AgentModelHostAdapter("model-test", config=config, transport=transport)
        progress = []
        final, messages, calls = self._run(adapter, progress=lambda kind, text: progress.append((kind, text)))

        self.assertEqual(final, "严格协议完成。")
        self.assertEqual([item["id"] for item in calls], ["strict-read-1"])
        self.assertIn("value = 7", json.loads(calls[0]["result"])["data"])
        self.assertEqual(messages[-2]["tool_call_id"], "strict-read-1")
        self.assertNotIn(tool_envelope, [text for _kind, text in progress])
        self.assertTrue(all(not request.get("tools") for request in transport.requests))
        wire_history = transport.requests[1]["messages"]
        self.assertFalse(any(item.get("role") == "tool" for item in wire_history))
        self.assertFalse(any(item.get("tool_calls") for item in wire_history))
        observations = [
            json.loads(item["content"])
            for item in wire_history
            if item.get("role") == "user" and '"tool_observations"' in item.get("content", "")
        ]
        self.assertEqual(len(observations), 1)
        observation = observations[0]["tool_observations"][0]
        self.assertEqual(observation["tool_call_id"], "strict-read-1")
        self.assertEqual(observation["name"], "read_file")
        self.assertEqual(observation["arguments"], {"path": "strict.py"})
        self.assertIn("value = 7", observation["result"]["data"]["data"])
        self.assertFalse(any({"id", "created_at", "updated_at"} & set(item) for item in wire_history))

    def test_explicit_text_only_capability_never_exposes_or_executes_tools(self):
        class TextOnlyAdapter:
            capability = AgentModelCapability.TEXT_ONLY
            effective_capability = AgentModelCapability.TEXT_ONLY

            def __init__(self):
                self.requests = []

            def complete_turn(self, request, *, cancellation, on_delta):
                self.requests.append(request)
                return AgentTurn(
                    tool_calls=(ToolCall("text-only-read", "read_file", {"path": "secret.py"}),),
                    finish_reason="tool_calls",
                )

        target = self.root / "secret.py"
        target.write_text("private = True\n", encoding="utf-8")
        adapter = TextOnlyAdapter()
        final, _messages, calls = self._run(adapter)

        self.assertEqual(
            final,
            "当前模型不支持工具调用，仅提供建议，本轮未执行文件操作。",
        )
        self.assertEqual(adapter.requests[0].tools, ())
        self.assertEqual(calls, [])
        self.assertTrue(target.exists())

        class TextOnlyTextAdapter(TextOnlyAdapter):
            def complete_turn(self, request, *, cancellation, on_delta):
                self.requests.append(request)
                return AgentTurn(text="建议先检查入口文件，再逐步修改。")

        text_adapter = TextOnlyTextAdapter()
        text_final, text_messages, text_calls = self._run(text_adapter)
        self.assertTrue(text_final.startswith("当前模型不支持工具调用，仅提供建议，本轮未执行文件操作。"))
        self.assertEqual(text_final.count("模型建议："), 1)
        self.assertEqual(text_calls, [])
        self.assertEqual(
            [item["content"] for item in text_messages if item.get("role") == "assistant"],
            [text_final],
        )

    def test_read_file_returns_bounded_resumable_ranges_for_large_files(self):
        content = "a" * 40_000
        (self.root / "large.py").write_text(content, encoding="utf-8")
        registry = ProgrammingToolRegistry(str(self.root))
        first = registry.execute(
            ToolCall("read-1", "read_file", {"path": "large.py"}),
            context=type("Context", (), {"domain": "programming"})(),
            cancellation=CancellationToken(),
        )
        self.assertTrue(first.ok)
        self.assertEqual(len(first.data["data"]), MAX_READ_FILE_CHARS)
        self.assertEqual(first.data["next_offset"], MAX_READ_FILE_CHARS)
        self.assertEqual(first.data["total_chars"], 40_000)

    def test_write_fails_closed_without_confirmation(self):
        target = self.root / "new.py"
        adapter = _ScriptedAdapter([
            {"turn": AgentTurn(tool_calls=(ToolCall("write-1", "write_file", {"path": "new.py", "content": "print(1)\n"}),), finish_reason="tool_calls")},
            {"turn": AgentTurn(text="没有确认，因此文件未写入。", finish_reason="stop")},
        ])
        final, _messages, calls = self._run(adapter)
        self.assertEqual(final, "没有确认，因此文件未写入。")
        self.assertFalse(target.exists())
        self.assertEqual(len(calls), 1)
        result = json.loads(calls[0]["result"])
        self.assertIn("写入文件需要用户确认", result["error"])

    def test_cancel_after_approval_prevents_write(self):
        target = self.root / "cancel.py"
        token = CancellationToken()
        adapter = _ScriptedAdapter([
            {"turn": AgentTurn(tool_calls=(ToolCall("write-2", "write_file", {"path": "cancel.py", "content": "print(2)\n"}),), finish_reason="tool_calls")},
        ])

        def approve_and_cancel(_title, _diff):
            token.cancel("cancelled")
            return True

        final, _messages, calls = self._run(adapter, confirm_cb=approve_and_cancel, cancellation=token)
        self.assertIn("停止", final)
        self.assertFalse(target.exists())
        self.assertEqual(calls, [])

    def test_confirmed_edit_rejects_a_concurrent_original_file_change(self):
        target = self.root / "stale.py"
        target.write_text("before = 1\n", encoding="utf-8")
        adapter = _ScriptedAdapter([
            {"turn": AgentTurn(tool_calls=(ToolCall("edit-1", "edit_file", {"path": "stale.py", "old_str": "before = 1\n", "new_str": "after = 2\n"}),), finish_reason="tool_calls")},
            {"turn": AgentTurn(text="检测到文件变化。", finish_reason="stop")},
        ])
        confirmations = []

        def change_while_confirming(_title, _diff):
            confirmations.append(_diff)
            target.write_text("external = 3\n", encoding="utf-8")
            return True

        self._run(adapter, confirm_cb=change_while_confirming)
        self.assertEqual(len(confirmations), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "external = 3\n")

    def test_confirmed_edit_preserves_utf8_bom_and_crlf(self):
        target = self.root / "windows.py"
        target.write_bytes(b"\xef\xbb\xbfvalue = 1\r\n# keep \xe4\xb8\xad\xe6\x96\x87\r\n")
        adapter = _ScriptedAdapter([
            {"turn": AgentTurn(tool_calls=(ToolCall(
                "edit-format", "edit_file",
                {"path": "windows.py", "old_str": "value = 1\n", "new_str": "value = 2\n"},
            ),), finish_reason="tool_calls")},
            {"turn": AgentTurn(text="已保留文件格式。", finish_reason="stop")},
        ])
        final, _messages, calls = self._run(adapter, confirm_cb=lambda _title, _diff: True)
        self.assertEqual(final, "已保留文件格式。")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            target.read_bytes(),
            b"\xef\xbb\xbfvalue = 2\r\n# keep \xe4\xb8\xad\xe6\x96\x87\r\n",
        )

    def test_confirmed_gb18030_edit_roundtrips_and_rejects_unencodable_text(self):
        target = self.root / "legacy.py"
        original_text = "value = 1\r\n# 中文保持\r\n"
        original = original_text.encode("gb18030")
        target.write_bytes(original)
        adapter = _ScriptedAdapter([
            {"turn": AgentTurn(tool_calls=(ToolCall(
                "edit-gb", "edit_file",
                {"path": "legacy.py", "old_str": "value = 1\n", "new_str": "value = 2\n"},
            ),), finish_reason="tool_calls")},
            {"turn": AgentTurn(text="已安全编辑。", finish_reason="stop")},
        ])
        final, _messages, calls = self._run(adapter, confirm_cb=lambda _title, _diff: True)
        self.assertEqual(final, "已安全编辑。")
        self.assertEqual(target.read_bytes(), "value = 2\r\n# 中文保持\r\n".encode("gb18030"))
        self.assertEqual(len(calls), 1)

        target.write_bytes(original)
        no_confirmations = []
        result = execute_tool(
            "edit_file",
            {"path": "legacy.py", "old_str": "value = 1\n", "new_str": "value = \ud800\n"},
            str(self.root),
            confirm_cb=lambda title, diff: no_confirmations.append((title, diff)) or True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("原文件编码", result["error"])
        self.assertEqual(no_confirmations, [])
        self.assertEqual(target.read_bytes(), original)

    def test_natural_language_json_is_never_executed_as_a_tool(self):
        proposed = '{"tool":"write_file","args":{"path":"bad.py","content":"x"}}'
        adapter = _ScriptedAdapter([{
            "turn": AgentTurn(text=proposed, finish_reason="stop"),
            "deltas": [ModelDelta(kind="text_delta", text=proposed)],
        }])
        final, _messages, calls = self._run(adapter)
        self.assertEqual(final, proposed)
        self.assertFalse((self.root / "bad.py").exists())
        self.assertEqual(calls, [])

    def test_conflicting_reused_native_call_id_blocks_entire_batch(self):
        adapter = _ScriptedAdapter([{
            "turn": AgentTurn(tool_calls=(
                ToolCall("same", "read_file", {"path": "one.py"}),
                ToolCall("same", "read_file", {"path": "two.py"}),
            ), finish_reason="tool_calls"),
        }])
        final, _messages, calls = self._run(adapter)
        self.assertIn("TOOL_CALL_CONFLICT", final)
        self.assertEqual(calls, [])

    def test_data_center_and_programming_registries_are_isolated(self):
        programming_names = {item["function"]["name"] for item in ProgrammingToolRegistry(str(self.root)).definitions()}
        database_names = {item["function"]["name"] for item in DataCenterToolRegistry(object()).definitions()}
        self.assertTrue({"read_file", "edit_file", "write_file"}.issubset(programming_names))
        self.assertTrue({"query_readonly", "search_objects"}.issubset(database_names))
        self.assertFalse(programming_names & database_names)


if __name__ == "__main__":
    unittest.main()
