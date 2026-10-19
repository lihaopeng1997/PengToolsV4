from __future__ import annotations

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import tools.agent_core.memory as memory_module
from tools.agent_core.memory import ContextMemory, MemoryBinding, build_memory, decode_memory


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _tool_exchange(name: str, call_id: str, data: dict, *, ok: bool = True, code: str = "OK", evidence: str = "ev-1") -> list[dict]:
    envelope = {
        "ok": ok,
        "code": code,
        "data": data,
        "evidence_id": evidence,
        "query_id": None,
        "scope": None,
        "truncated": False,
        "limits": {},
        "elapsed_ms": 1,
    }
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(envelope, ensure_ascii=False)},
    ]


def _file_data(tool: str, path: str, *, action: str = "edit", digest: str | None = None, existed: bool = True) -> dict:
    return {
        "ok": True,
        "success": True,
        "tool": tool,
        "path": path,
        "action": action,
        "sha256": digest or _sha(path),
        "before_sha256": _sha("before:" + path),
        "after_sha256": _sha("after:" + path),
        "precondition_sha256": _sha("precondition:" + path),
        "existed": existed,
    }


def _binding(**overrides) -> MemoryBinding:
    values = {
        "workspace_id": "workspace-1",
        "conversation_id": "conversation-1",
        "workspace_revision": 4,
        "conversation_revision": 1,
        "profile_revision": "profile-rev-a",
    }
    values.update(overrides)
    return MemoryBinding(**values)


def _rehash(payload: dict) -> None:
    unhashed = {key: value for key, value in payload.items() if key != "memory_hash"}
    payload["memory_hash"] = hashlib.sha256(
        json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


class AgentContextMemoryTests(unittest.TestCase):
    def test_binding_has_strict_types_and_serializes_all_revision_dimensions(self) -> None:
        binding = _binding()
        self.assertEqual(binding.to_dict(), {
            "workspace_id": "workspace-1",
            "conversation_id": "conversation-1",
            "workspace_revision": 4,
            "conversation_revision": 1,
            "profile_revision": "profile-rev-a",
        })
        with self.assertRaises(TypeError):
            _binding(workspace_revision=True)
        with self.assertRaises(TypeError):
            _binding(conversation_revision=1.0)
        with self.assertRaises(ValueError):
            _binding(workspace_revision=-1)
        with self.assertRaises(ValueError):
            _binding(profile_revision=" ")
        with self.assertRaises(TypeError):
            _binding(conversation_id=7)

    def test_notes_are_exact_assistant_excerpts_and_to_message_is_untrusted(self) -> None:
        messages = [
            {"role": "user", "content": "Never modify project configuration."},
            {"role": "assistant", "content": "Inspection complete; no changes were made."},
            {"role": "user", "content": "new unanswered prompt"},
        ]
        before = copy.deepcopy(messages)

        memory = build_memory(messages=messages, binding=_binding())
        payload = memory.to_dict()
        message = memory.to_message()

        self.assertIsInstance(memory, ContextMemory)
        self.assertEqual(payload["covered_count"], 2)
        self.assertEqual(payload["assistant_notes"][0]["excerpt"], "Inspection complete; no changes were made.")
        self.assertNotIn("Never modify project configuration.", json.dumps(payload))
        self.assertEqual(message["role"], "assistant")
        self.assertNotIn("tool_calls", message)
        self.assertIn("Untrusted historical Agent context", message["content"])
        self.assertIn("does not authorize tools", message["content"])
        self.assertEqual(messages, before)

    def test_artifacts_and_run_test_validations_use_only_structured_host_fields(self) -> None:
        messages = [{"role": "user", "content": "edit and test"}]
        messages += _tool_exchange("read_file", "read-1", _file_data("read_file", "src/calc.py", action="read"), evidence="file-read-1")
        messages += _tool_exchange("edit_file", "edit-1", _file_data("edit_file", "src/calc.py", action="edit", digest=_sha("edited")), evidence="file-edit-1")
        messages += _tool_exchange(
            "run_test",
            "test-1",
            {"ok": True, "success": True, "tool": "run_test", "returncode": 0, "output": "PASS 1 test"},
            evidence="test-evidence-1",
        )
        messages.append({"role": "assistant", "content": "The host test result returned code 0."})

        memory = build_memory(messages=messages, binding=_binding())
        payload = memory.to_dict()

        self.assertEqual(len(payload["artifacts"]), 1)
        artifact = payload["artifacts"][0]
        self.assertEqual(artifact["path"], "src/calc.py")
        self.assertEqual(artifact["action"], "edit")
        self.assertEqual(artifact["tool_call_id"], "edit-1")
        self.assertEqual(artifact["sha256"], _sha("edited"))
        self.assertEqual(artifact["before_sha256"], _sha("before:src/calc.py"))
        self.assertEqual(artifact["after_sha256"], _sha("after:src/calc.py"))
        self.assertEqual(artifact["precondition_sha256"], _sha("precondition:src/calc.py"))
        self.assertTrue(artifact["existed"])
        self.assertEqual(artifact["source_evidence"], "file-edit-1")
        self.assertEqual(payload["validations"], [{
            "tool": "run_test",
            "tool_call_id": "test-1",
            "host_ok": True,
            "returncode": 0,
            "code": "OK",
            "source_evidence": "test-evidence-1",
        }])
        self.assertEqual(payload["omitted"]["artifacts"], 1)

    def test_incremental_build_processes_only_new_tail_and_keeps_old_evidence(self) -> None:
        first_messages = [{"role": "user", "content": "edit first"}]
        first_messages += _tool_exchange("edit_file", "edit-first", _file_data("edit_file", "src/first.py"), evidence="ev-first")
        first_messages.append({"role": "assistant", "content": "First edit receipt recorded."})
        previous = build_memory(messages=first_messages, binding=_binding())

        tail = [{"role": "user", "content": "read second"}]
        tail += _tool_exchange("read_file", "read-second", _file_data("read_file", "src/second.py", action="read"), evidence="ev-second")
        tail.append({"role": "assistant", "content": "Second read receipt recorded."})
        combined = first_messages + tail
        seen_tail_sizes: list[int] = []
        original_extract = memory_module._extract

        def count_extracted(messages, units, covered_count, *, index_base):
            seen_tail_sizes.append(len(messages))
            return original_extract(messages, units, covered_count, index_base=index_base)

        with patch.object(memory_module, "_extract", side_effect=count_extracted):
            current = build_memory(
                messages=combined,
                binding=_binding(conversation_revision=2),
                previous=previous,
            )

        payload = current.to_dict()
        self.assertEqual(seen_tail_sizes, [len(tail)])
        self.assertEqual(current.cycle, previous.cycle + 2)
        self.assertEqual(payload["parent_hash"], previous.covered_hash)
        self.assertEqual(payload["parent_covered_count"], previous.covered_count)
        self.assertEqual([item["source_evidence"] for item in payload["artifacts"]], ["ev-first", "ev-second"])
        self.assertEqual([item["excerpt"] for item in payload["assistant_notes"]], [
            "First edit receipt recorded.", "Second read receipt recorded."
        ])

    def test_incremental_shorter_artifact_rebuilds_evicted_source_prefix_across_byte_caps(self) -> None:
        messages = [{"role": "user", "content": "retain the complete source trail"}]
        messages.extend(
            {"role": "assistant", "content": f"note-{index:02d}:" + ("n" * 504)}
            for index in range(12)
        )
        messages += _tool_exchange(
            "edit_file",
            "edit-old",
            _file_data("edit_file", "src/calc.py", action="edit"),
            evidence="ev-edit-old",
        )
        messages.append({"role": "assistant", "content": "Edit receipt retained."})
        original_messages = copy.deepcopy(messages)

        read_data = {
            "ok": True,
            "success": True,
            "tool": "read_file",
            "path": "src/calc.py",
            "action": "read",
            "existed": True,
            # This host receipt intentionally has no hash fields.
        }
        tail = _tool_exchange("read_file", "read-new", read_data, evidence="ev-read-new")
        tail.append({"role": "assistant", "content": "Read receipt retained."})

        fallback_caps: list[int] = []
        reclaimed_note_caps: list[int] = []
        checked_caps: list[int] = []
        source_builder = memory_module._build_from_source
        for cap in range(2200, 8000, 100):
            with self.subTest(max_bytes=cap):
                try:
                    previous = build_memory(messages=messages, binding=_binding(), max_bytes=cap)
                except ValueError as error:
                    if str(error) == "memory_header_too_large":
                        continue
                    raise
                previous_payload = previous.to_dict()
                previous_artifacts = {item["path"]: item for item in previous_payload["artifacts"]}
                old_artifact = previous_artifacts.get("src/calc.py")
                if not previous_payload["omitted"]["assistant_notes"] or old_artifact is None:
                    continue

                with patch.object(memory_module, "_build_from_source", wraps=source_builder) as rebuild:
                    current = build_memory(
                        messages=messages + tail,
                        binding=_binding(conversation_revision=2),
                        previous=previous,
                        max_bytes=cap,
                    )
                self.assertLess(
                    len(memory_module._json_bytes(current.to_dict()["artifacts"][-1])),
                    len(memory_module._json_bytes(old_artifact)),
                )
                self.assertGreater(rebuild.call_count, 0)
                fallback_caps.append(cap)
                checked_caps.append(cap)
                if len(current.to_dict()["assistant_notes"]) > len(previous_payload["assistant_notes"]):
                    reclaimed_note_caps.append(cap)

                self.assertIsNotNone(
                    decode_memory(
                        current.to_dict(),
                        messages=messages + tail,
                        binding=_binding(conversation_revision=2),
                    )
                )
                fresh = build_memory(
                    messages=messages + tail,
                    binding=_binding(conversation_revision=2),
                    max_bytes=cap,
                )
                self.assertEqual(current.to_dict(), fresh.to_dict())

        self.assertEqual(messages, original_messages)
        self.assertGreater(len(checked_caps), 0, "fixture must exercise the evicted-note and retained-artifact state")
        self.assertEqual(fallback_caps, checked_caps, "shrink fallback must rebuild from source, not merge only the tail")
        self.assertGreater(len(reclaimed_note_caps), 0, "at least one cap must demonstrate notes reclaimed after shrink")

    def test_incremental_shorter_artifact_rebuilds_when_old_artifact_was_fully_evicted(self) -> None:
        messages = [{"role": "user", "content": "retain source notes and artifact"}]
        messages.extend({"role": "assistant", "content": str(index) + ("x" * 8)} for index in range(12))
        messages += _tool_exchange(
            "edit_file",
            "edit-old",
            _file_data("edit_file", "src/calc.py", action="edit"),
            evidence="ev-edit-old",
        )
        messages.append({"role": "assistant", "content": "Edit receipt recorded."})
        original_messages = copy.deepcopy(messages)

        read_data = {
            "ok": True,
            "success": True,
            "tool": "read_file",
            "path": "src/calc.py",
            "action": "read",
            "existed": True,
        }
        tail = _tool_exchange("read_file", "read-new", read_data, evidence="ev-read-new")
        tail.append({"role": "assistant", "content": "Read receipt recorded."})
        source_builder = memory_module._build_from_source

        for cap in (1020, 1030):
            with self.subTest(max_bytes=cap):
                previous = build_memory(messages=messages, binding=_binding(), max_bytes=cap)
                previous_payload = previous.to_dict()
                self.assertEqual(previous_payload["artifacts"], [])
                self.assertGreater(previous_payload["omitted"]["artifacts"], 0)

                full_messages = messages + tail
                with patch.object(memory_module, "_build_from_source", wraps=source_builder) as rebuild:
                    current = build_memory(
                        messages=full_messages,
                        binding=_binding(conversation_revision=2),
                        previous=previous,
                        max_bytes=cap,
                    )
                self.assertEqual(rebuild.call_count, 1)
                self.assertIsNotNone(
                    decode_memory(
                        current.to_dict(),
                        messages=full_messages,
                        binding=_binding(conversation_revision=2),
                    )
                )
                fresh = build_memory(
                    messages=full_messages,
                    binding=_binding(conversation_revision=2),
                    max_bytes=cap,
                )
                self.assertEqual(current.to_dict(), fresh.to_dict())

        self.assertEqual(messages, original_messages)

    def test_previous_binding_or_prefix_drift_falls_back_to_fresh_build(self) -> None:
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "second answer"},
        ]
        previous = build_memory(messages=messages[:2], binding=_binding())
        variations = [
            _binding(workspace_id="workspace-other", conversation_revision=2),
            _binding(conversation_id="conversation-other", conversation_revision=2),
            _binding(workspace_revision=5, conversation_revision=2),
            _binding(profile_revision="profile-rev-b", conversation_revision=2),
            _binding(conversation_revision=0),
        ]
        for changed in variations:
            with self.subTest(binding=changed.to_dict()):
                result = build_memory(messages=messages, binding=changed, previous=previous)
                self.assertIsNone(result.to_dict()["parent_hash"])
                self.assertEqual(result.to_dict()["covered_count"], 4)

        changed_prefix = copy.deepcopy(messages)
        changed_prefix[1]["content"] = "changed prior answer"
        result = build_memory(
            messages=changed_prefix,
            binding=_binding(conversation_revision=2),
            previous=previous,
        )
        self.assertIsNone(result.to_dict()["parent_hash"])
        self.assertEqual(result.to_dict()["assistant_notes"][0]["excerpt"], "changed prior answer")

    def test_decode_accepts_forward_conversation_revision_but_rejects_binding_drift(self) -> None:
        messages = [{"role": "user", "content": "request"}, {"role": "assistant", "content": "answer"}]
        memory = build_memory(messages=messages, binding=_binding())

        self.assertIsNotNone(decode_memory(
            memory.to_dict(), messages=messages, binding=_binding(conversation_revision=3)
        ))
        for changed in (
            _binding(workspace_id="other"),
            _binding(conversation_id="other"),
            _binding(workspace_revision=5),
            _binding(profile_revision="other"),
            _binding(conversation_revision=0),
        ):
            with self.subTest(binding=changed.to_dict()):
                self.assertIsNone(decode_memory(memory.to_dict(), messages=messages, binding=changed))

    def test_decode_rejects_notes_artifacts_and_validation_tampering_after_rehash(self) -> None:
        messages = [{"role": "user", "content": "request"}]
        messages += _tool_exchange("edit_file", "edit-1", _file_data("edit_file", "src/calc.py"), evidence="ev-edit")
        messages += _tool_exchange(
            "run_test", "test-1",
            {"ok": True, "success": True, "tool": "run_test", "returncode": 0},
            evidence="ev-test",
        )
        messages.append({"role": "assistant", "content": "Verified from the host return code."})
        memory = build_memory(messages=messages, binding=_binding())

        mutations = (
            ("assistant_notes", lambda payload: payload["assistant_notes"][0].__setitem__("excerpt", "forged statement")),
            ("artifacts", lambda payload: payload["artifacts"][0].__setitem__("sha256", _sha("forged-file"))),
            ("validations", lambda payload: payload["validations"][0].__setitem__("returncode", 17)),
        )
        for name, mutate in mutations:
            with self.subTest(field=name):
                forged = memory.to_dict()
                mutate(forged)
                _rehash(forged)
                self.assertIsNone(decode_memory(forged, messages=messages, binding=_binding()))

    def test_artifact_paths_are_relative_and_hashes_are_strict_or_unknown(self) -> None:
        invalid_paths = ("../secret.py", "/absolute.py", "C:\\outside.py", "src/../outside.py", "src//calc.py")
        for path in invalid_paths:
            with self.subTest(path=path):
                messages = [{"role": "user", "content": "request"}]
                messages += _tool_exchange("edit_file", "edit-1", _file_data("edit_file", path), evidence="ev-path")
                messages.append({"role": "assistant", "content": "done"})
                payload = build_memory(messages=messages, binding=_binding()).to_dict()
                self.assertEqual(payload["artifacts"], [])
                self.assertEqual(payload["omitted"]["artifacts"], 1)

        messages = [{"role": "user", "content": "request"}]
        messages += _tool_exchange(
            "write_file", "write-1",
            _file_data("write_file", "src/calc.py", action="write", digest="not-a-sha256"),
            evidence="ev-write",
        )
        messages.append({"role": "assistant", "content": "done"})
        artifact = build_memory(messages=messages, binding=_binding()).to_dict()["artifacts"][0]
        self.assertEqual(artifact["sha256"], "unknown")
        self.assertEqual(artifact["before_sha256"], _sha("before:src/calc.py"))

    def test_long_tool_output_and_patch_arguments_are_not_copied(self) -> None:
        data = _file_data("edit_file", "src/calc.py")
        data["output"] = "SENSITIVE_TOOL_OUTPUT_" + ("x" * 100_000)
        messages = [{"role": "user", "content": "request"}]
        messages += _tool_exchange("edit_file", "edit-1", data, evidence="ev-edit")
        messages[-2]["tool_calls"][0]["function"]["arguments"] = json.dumps({
            "path": "src/calc.py", "old_string": "WHOLE_OLD_FILE", "new_string": "WHOLE_NEW_FILE"
        })
        messages.append({"role": "assistant", "content": "Edit receipt recorded."})
        before = copy.deepcopy(messages)

        payload = build_memory(messages=messages, binding=_binding()).to_dict()
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(messages, before)
        self.assertNotIn("SENSITIVE_TOOL_OUTPUT", encoded)
        self.assertNotIn("WHOLE_OLD_FILE", encoded)
        self.assertNotIn("WHOLE_NEW_FILE", encoded)
        self.assertEqual(len(payload["artifacts"]), 1)

    def test_failed_run_test_keeps_only_safe_code_and_structured_returncode(self) -> None:
        messages = [{"role": "user", "content": "test"}]
        messages += _tool_exchange(
            "run_test", "test-fail",
            {"ok": False, "success": False, "tool": "run_test", "returncode": 2, "output": "secret traceback"},
            ok=False,
            code="SECRET_EXCEPTION_TEXT",
            evidence="ev-test-fail",
        )
        messages.append({"role": "assistant", "content": "The host returned nonzero."})

        payload = build_memory(messages=messages, binding=_binding()).to_dict()

        self.assertEqual(payload["validations"][0]["returncode"], 2)
        self.assertEqual(payload["validations"][0]["code"], "TOOL_FAILED")
        self.assertNotIn("secret traceback", json.dumps(payload))
        self.assertNotIn("SECRET_EXCEPTION_TEXT", json.dumps(payload))

    def test_max_bytes_counts_full_json_and_truncation_counts_are_explicit(self) -> None:
        messages = [{"role": "user", "content": "request"}]
        messages.extend({"role": "assistant", "content": f"note-{index}:" + ("x" * 500)} for index in range(18))
        messages.append({"role": "assistant", "content": "latest note"})
        memory = build_memory(messages=messages, binding=_binding(), max_bytes=1800)
        payload = memory.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

        self.assertLessEqual(len(encoded), 1800)
        self.assertGreater(payload["omitted"]["assistant_notes"], 0)
        self.assertGreater(payload["omitted"]["assistant_note_chars"], 0)
        self.assertEqual(payload["assistant_notes"][-1]["excerpt"], "latest note")
        # The stored max_bytes is part of the cache; use the same bound when decoding.
        self.assertIsNotNone(decode_memory(payload, messages=messages, binding=_binding()))

    def test_old_artifacts_are_truncated_before_latest_and_header_failure_is_stable(self) -> None:
        messages = [{"role": "user", "content": "write files"}]
        for index in range(12):
            path = f"src/generated-{index}.py"
            messages += _tool_exchange(
                "write_file", f"write-{index}", _file_data("write_file", path, action="write"), evidence=f"ev-{index}"
            )
        messages.append({"role": "assistant", "content": "All writes have host receipts."})
        payload = build_memory(messages=messages, binding=_binding(), max_bytes=2200).to_dict()
        paths = [item["path"] for item in payload["artifacts"]]
        self.assertGreater(payload["omitted"]["artifacts"], 0)
        self.assertIn("src/generated-11.py", paths)
        self.assertNotIn("src/generated-0.py", paths)
        with self.assertRaisesRegex(ValueError, "memory_header_too_large"):
            build_memory(messages=messages, binding=_binding(), max_bytes=20)

    def test_malformed_pending_tool_history_and_private_field_are_rejected(self) -> None:
        cases = (
            [{"role": "assistant", "tool_calls": [{"id": "pending", "function": {"name": "read_file"}}]}],
            [{"role": "tool", "tool_call_id": "orphan", "content": "{}"}],
            [
                {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read_file"}}]},
                {"role": "tool", "tool_call_id": "a", "content": "{}"},
                {"role": "tool", "tool_call_id": "a", "content": "{}"},
            ],
            [
                {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read_file"}}]},
                {"role": "tool", "tool_call_id": " a ", "content": "{}"},
            ],
        )
        for messages in cases:
            with self.subTest(messages=messages):
                with self.assertRaisesRegex(ValueError, "incomplete_history"):
                    build_memory(messages=messages, binding=_binding())
        with self.assertRaisesRegex(ValueError, "private_reasoning_content"):
            build_memory(
                messages=[{"role": "assistant", "content": "public", "reasoning_content": "private"}],
                binding=_binding(),
            )

    def test_input_and_return_values_are_deeply_isolated(self) -> None:
        messages = [{"role": "user", "content": {"nested": [1, {"value": "source"}]}}]
        messages.append({"role": "assistant", "content": "answer"})
        before = copy.deepcopy(messages)
        memory = build_memory(messages=messages, binding=_binding())

        exported = memory.to_dict()
        exported["assistant_notes"][0]["excerpt"] = "tampered copy"
        first_message = memory.to_message()
        first_message["content"] = "tampered message"

        self.assertEqual(messages, before)
        self.assertEqual(memory.to_dict()["assistant_notes"][0]["excerpt"], "answer")
        self.assertIn("answer", memory.to_message()["content"])


if __name__ == "__main__":
    unittest.main()
