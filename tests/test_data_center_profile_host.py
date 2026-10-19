"""TEMP-only behaviour tests for immutable data-center profile snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.data_center.profile_host import DataCenterProfileHost, ProfileHostError


def _connection() -> dict:
    return {
        "id": "temp-profile-1",
        "name": "TEMP connection",
        "provider": "mysql",
        "dialect": "mysql",
        "mode": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "database": "sample",
        "username": "temp-user",
        "password": "TEMP_CONNECTION_SECRET",
        "future_extension": {"preserve": True},
    }


def _model() -> dict:
    return {
        "id": "temp-model-1",
        "name": "TEMP model",
        "enabled": True,
        "base_url": "https://model.invalid/v1",
        "token": "TEMP_MODEL_SECRET",
        "model": "temporary-model",
        "agent_capability": "native_tools",
        "custom_provider_field": {"keep": "this"},
    }


class DataCenterProfileHostTests(unittest.TestCase):
    def test_exact_ids_unknown_fields_and_revision_drift_are_preserved(self) -> None:
        profile = _connection()
        model = _model()
        connections = {"items": [profile]}
        catalog = {"active_model_id": model["id"], "items": [model]}
        host = DataCenterProfileHost(
            connections_loader=lambda: connections,
            model_catalog_loader=lambda: catalog,
        )

        target = host.capture_connection(profile, database="sample", schema_allowlist=("sales",))
        choices, active_id = host.list_model_choices()
        self.assertEqual((len(choices), active_id), (1, model["id"]))
        self.assertEqual(choices[0].model_config_id, model["id"])
        self.assertNotIn("TEMP_MODEL_SECRET", repr(choices))
        self.assertNotIn("model.invalid", repr(choices))
        self.assertNotIn("TEMP_CONNECTION_SECRET", repr(target))

        loaded = host.load_connection_profile(profile["id"], target.revision)
        self.assertEqual(loaded["future_extension"], {"preserve": True})
        self.assertEqual(loaded["password"], profile["password"])
        loaded_model = host.load_model_config(model["id"], choices[0].revision)
        self.assertEqual(loaded_model["custom_provider_field"], {"keep": "this"})

        # A stable ID cannot silently pick up an edited endpoint/model after
        # the UI captured its model revision.
        catalog["items"][0] = {**model, "base_url": "https://changed.invalid/v1", "model": "changed"}
        with self.assertRaises(ProfileHostError) as model_error:
            host.load_model_config(model["id"], choices[0].revision)
        self.assertEqual(model_error.exception.code, "MODEL_REVISION_MISMATCH")

        # Connection identity is fixed by the same worker-side revision gate.
        connections["items"][0] = {**profile, "host": "changed.internal"}
        with self.assertRaises(ProfileHostError) as profile_error:
            host.load_connection_profile(profile["id"], target.revision)
        self.assertEqual(profile_error.exception.code, "PROFILE_MISMATCH")

    def test_default_readers_do_not_normalize_or_write_legacy_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pengtools-profile-host-") as folder:
            root = Path(folder)
            connection_path = root / "connections.json"
            model_path = root / "models.json"
            connection_bytes = json.dumps(
                {"items": [_connection()]}, ensure_ascii=False, indent=2,
            ).encode("utf-8")
            model_bytes = json.dumps(
                {"active_model_id": "temp-model-1", "items": [_model()]},
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            connection_path.write_bytes(connection_bytes)
            model_path.write_bytes(model_bytes)

            import config

            host = DataCenterProfileHost()
            with (
                patch.object(config, "HARNESS_CONNECTIONS_FILE", str(connection_path)),
                patch.object(config, "AI_LOCAL_FILE", str(model_path)),
            ):
                connection = host.load_connection_profile(
                    "temp-profile-1",
                    host.capture_connection(_connection(), database="sample").revision,
                )
                choices, active_id = host.list_model_choices()
                model = host.load_model_config("temp-model-1", choices[0].revision)

            self.assertEqual(connection["future_extension"], {"preserve": True})
            self.assertEqual(model["custom_provider_field"], {"keep": "this"})
            self.assertEqual(active_id, "temp-model-1")
            self.assertEqual(connection_path.read_bytes(), connection_bytes)
            self.assertEqual(model_path.read_bytes(), model_bytes)


if __name__ == "__main__":
    unittest.main()
