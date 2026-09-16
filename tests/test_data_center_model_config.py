from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.data_center.model_config import (
    AgentModelCapability,
    AgentModelConfigError,
    build_agent_model_snapshot,
    load_agent_model_profile,
    load_agent_model_snapshot,
)


def _config(config_id: str = "cfg-selected", **extra):
    value = {
        "id": config_id,
        "enabled": True,
        "base_url": "http://10.128.1.20:8000/v1",
        "model": "qwen-agent",
        "token": "MODEL_TOKEN_SHOULD_STAY_PRIVATE",
        "ssl_verify": True,
        "timeout_seconds": 120,
    }
    value.update(extra)
    return value


class AgentModelConfigTests(unittest.TestCase):
    def test_loader_receives_selected_id_and_snapshot_uses_model_name(self) -> None:
        received = []

        def loader(selected_id):
            received.append(selected_id)
            return _config(selected_id, agent_capability="unknown")

        snapshot = load_agent_model_snapshot("cfg-selected", loader=loader)

        self.assertEqual(received, ["cfg-selected"])
        self.assertEqual(snapshot.model_config_id, "cfg-selected")
        self.assertEqual(snapshot.model, "qwen-agent")
        self.assertNotEqual(snapshot.model, snapshot.model_config_id)
        self.assertEqual(snapshot.capability, AgentModelCapability.UNKNOWN)
        self.assertEqual(snapshot.effective_capability, AgentModelCapability.TEXT_ONLY)
        self.assertFalse(snapshot.tools_enabled)

    def test_public_snapshot_does_not_contain_endpoint_or_token(self) -> None:
        secret = "MODEL_TOKEN_SHOULD_STAY_PRIVATE"
        endpoint = "http://10.128.1.20:8000/v1"
        snapshot = load_agent_model_snapshot(
            "cfg-selected",
            loader=lambda _selected: _config("cfg-selected", token=secret, base_url=endpoint),
        )

        public = snapshot.as_public_dict()
        self.assertNotIn("token", public)
        self.assertNotIn("base_url", public)
        self.assertNotIn(secret, repr(snapshot))
        self.assertNotIn(secret, repr(public))
        self.assertNotIn(endpoint, repr(snapshot))
        self.assertNotIn(endpoint, repr(public))

    def test_default_loader_uses_exact_model_config_id(self) -> None:
        with patch(
            "tools.intranet_llm._cfg_for_call",
            return_value=_config("cfg-from-loader"),
        ) as mocked:
            snapshot = load_agent_model_snapshot("cfg-from-loader")

        mocked.assert_called_once_with(model_config_id="cfg-from-loader")
        self.assertEqual(snapshot.model, "qwen-agent")

    def test_mismatched_loader_id_is_rejected(self) -> None:
        with self.assertRaises(AgentModelConfigError):
            load_agent_model_snapshot(
                "cfg-selected",
                loader=lambda _selected: _config("a-different-config"),
            )

    def test_endpoint_is_required_only_for_private_profile(self) -> None:
        with self.assertRaises(AgentModelConfigError):
            load_agent_model_profile(
                "cfg-selected",
                loader=lambda _selected: _config("cfg-selected", enabled=False),
            )

        snapshot, private_config = load_agent_model_profile(
            "cfg-selected",
            loader=lambda _selected: _config("cfg-selected", enabled=False),
            require_endpoint=False,
        )
        self.assertEqual(snapshot.model, "qwen-agent")
        self.assertEqual(private_config["id"], "cfg-selected")

    def test_injected_config_snapshot_remains_public_safe(self) -> None:
        snapshot = build_agent_model_snapshot(
            "cfg-selected",
            _config("cfg-selected", agent_capability="native_tools"),
        )
        self.assertEqual(snapshot.capability, AgentModelCapability.NATIVE_TOOLS)
        self.assertTrue(snapshot.tools_enabled)
        self.assertNotIn("MODEL_TOKEN_SHOULD_STAY_PRIVATE", repr(snapshot))

    def test_snapshot_carries_deadline_limits_and_reasoning_whitelist(self) -> None:
        snapshot = load_agent_model_snapshot(
            "cfg-selected",
            loader=lambda _selected: _config(
                "cfg-selected",
                timeout_seconds=0.25,
                agent_reasoning_fields=["reasoning", "reasoning"],
                agent_max_reasoning_bytes=17,
                agent_max_text_bytes=23,
                agent_max_tool_argument_bytes=29,
                agent_max_raw_buffer_bytes=31,
            ),
        )

        self.assertEqual(snapshot.deadline_seconds, 0.25)
        self.assertEqual(snapshot.timeout_seconds, 0.25)
        self.assertEqual(snapshot.reasoning_fields, ("reasoning",))
        self.assertEqual(snapshot.max_reasoning_bytes, 17)
        self.assertEqual(snapshot.max_text_bytes, 23)
        self.assertEqual(snapshot.max_tool_argument_bytes, 29)
        self.assertEqual(snapshot.max_raw_buffer_bytes, 31)


if __name__ == "__main__":
    unittest.main()
