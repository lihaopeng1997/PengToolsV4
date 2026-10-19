from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.agent_core import ModelAdapter, ModelAdapterProtocol, StreamingModelAdapter
from tools.agent_core.contracts import RunContext


class SharedContextTests(unittest.TestCase):
    def test_scope_is_deeply_copied_frozen_and_exported_as_a_detached_dict(self) -> None:
        source = {"workspace": {"labels": ["first"]}}
        context = RunContext("run-1", "tab-1", "model-1", domain="programming", scope=source)
        source["workspace"]["labels"].append("changed")
        source["new"] = True

        self.assertEqual(context.scope["workspace"]["labels"], ("first",))
        with self.assertRaises(TypeError):
            context.scope["workspace"]["extra"] = "blocked"
        with self.assertRaises(AttributeError):
            context.scope["workspace"]["labels"].append("blocked")

        exported = context.as_dict()
        exported["scope"]["workspace"]["labels"].append("detached")
        self.assertEqual(context.as_dict()["scope"]["workspace"]["labels"], ["first"])

    def test_scope_rejects_non_json_objects(self) -> None:
        with self.assertRaises(TypeError):
            RunContext("run-1", "tab-1", "model-1", scope={"unsafe": object()})
        with self.assertRaises(TypeError):
            RunContext("run-2", "tab-2", "model-1", scope={1: "non-string key"})
        with self.assertRaises(TypeError):
            RunContext("run-3", "tab-3", "model-1", scope={"unsafe": float("inf")})

    def test_public_adapter_names_and_legacy_patch_path_share_one_implementation(self) -> None:
        import tools.agent_core.model_adapter as shared_module
        import tools.data_center.model_adapter as legacy_module

        self.assertIs(ModelAdapter, ModelAdapterProtocol)
        self.assertIs(StreamingModelAdapter, shared_module.ModelAdapter)
        self.assertIs(legacy_module, shared_module)

        sentinel_transport = object()
        with patch(
            "tools.data_center.model_adapter.UrlLibStreamingTransport",
            return_value=sentinel_transport,
        ) as transport_factory:
            concrete = StreamingModelAdapter({"model": "synthetic"})
        transport_factory.assert_called_once()
        self.assertIs(concrete.transport, sentinel_transport)


if __name__ == "__main__":
    unittest.main()
