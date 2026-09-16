"""Public package-surface and import-safety checks for the data-center Agent."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DataCenterPackageSurfaceTests(unittest.TestCase):
    def test_package_import_smoke_does_not_load_runtime_integrations(self) -> None:
        """Importing the package must not resolve Qt, drivers, or live config."""

        probe = textwrap.dedent(
            """
            import builtins
            import socket
            import sys
            import urllib.request

            forbidden_roots = {
                "PyQt5",
                "PyQt6",
                "PySide2",
                "PySide6",
                "oracledb",
                "pymongo",
                "pymysql",
                "pyodbc",
                "redis",
                "sqlite3",
                "sqlalchemy",
            }
            forbidden_modules = {
                "tools.db_connect",
                "tools.db_contracts",
                "tools.intranet_llm",
            }
            original_import = builtins.__import__

            def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                if level == 0:
                    root = name.partition(".")[0]
                    if root in forbidden_roots or name in forbidden_modules:
                        raise AssertionError(f"forbidden import during package smoke: {name}")
                return original_import(name, globals, locals, fromlist, level)

            def forbidden_io(*args, **kwargs):
                raise AssertionError("package import attempted runtime network I/O")

            builtins.__import__ = guarded_import
            socket.create_connection = forbidden_io
            urllib.request.urlopen = forbidden_io
            urllib.request.build_opener = forbidden_io

            import tools.data_center as package
            from tools.data_center import contracts, model_adapter, tool_registry

            assert package.ModelAdapter is contracts.ModelAdapter
            assert package.StreamingModelAdapter is model_adapter.ModelAdapter
            assert package.ToolRegistry is contracts.ToolRegistry
            assert package.DataCenterToolRegistry is tool_registry.DataCenterToolRegistry
            assert "tools.intranet_llm" not in sys.modules
            assert "tools.db_connect" not in sys.modules
            assert not any(
                module.partition(".")[0] in forbidden_roots
                for module in sys.modules
            )
            print("package import smoke passed")
            """
        )
        environment = dict(os.environ)
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(result.stdout.strip(), "package import smoke passed")

    def test_protocol_and_concrete_aliases_keep_their_identity(self) -> None:
        import tools.data_center as package
        from tools.data_center import contracts, engine_adapters, model_adapter, query_executor, tool_registry

        self.assertIs(package.ModelAdapter, contracts.ModelAdapter)
        self.assertIs(package.ModelAdapterProtocol, contracts.ModelAdapter)
        self.assertIs(package.StreamingModelAdapter, model_adapter.ModelAdapter)
        self.assertIsNot(package.ModelAdapter, package.StreamingModelAdapter)
        self.assertEqual(package.ModelAdapter.__module__, "tools.data_center.contracts")
        self.assertEqual(package.StreamingModelAdapter.__module__, "tools.data_center.model_adapter")

        self.assertIs(package.ToolRegistry, contracts.ToolRegistry)
        self.assertIs(package.ToolRegistryProtocol, contracts.ToolRegistry)
        self.assertIs(package.DataCenterToolRegistry, tool_registry.DataCenterToolRegistry)
        self.assertIs(package.EngineCapabilities, engine_adapters.EngineCapability)
        self.assertIs(package.EngineReadOnlyQuery, engine_adapters.ReadOnlyQuery)
        self.assertIs(package.EngineReadOnlyRequest, engine_adapters.ReadOnlyRequest)
        self.assertIs(package.ReadOnlyRequest, engine_adapters.ReadOnlyRequest)
        self.assertIs(package.ReadOnlyQuery, query_executor.ReadOnlyQuery)
        self.assertIs(package.ExecutorReadOnlyQuery, query_executor.ReadOnlyQuery)

    def test_dc03_public_exports_keep_their_module_identity(self) -> None:
        import tools.data_center as package
        from tools.data_center import (
            host_model_adapter,
            model_config,
            readonly_driver_adapters,
            readonly_lease,
            readonly_nosql_clients,
        )

        exported = {
            "AgentModelCapability": model_config.AgentModelCapability,
            "AgentModelConfigError": model_config.AgentModelConfigError,
            "AgentModelHostAdapter": host_model_adapter.AgentModelHostAdapter,
            "AgentModelSnapshot": model_config.AgentModelSnapshot,
            "MongoReadOnlyFacade": readonly_nosql_clients.MongoReadOnlyFacade,
            "OpaqueCursorCodec": readonly_nosql_clients.OpaqueCursorCodec,
            "ReadOnlyDriverCapabilities": readonly_driver_adapters.ReadOnlyDriverCapabilities,
            "ReadOnlyInitializationError": readonly_driver_adapters.ReadOnlyInitializationError,
            "ReadOnlyLease": readonly_lease.ReadOnlyLease,
            "ReadOnlyLeaseError": readonly_lease.ReadOnlyLeaseError,
            "ReadOnlyLeaseFactory": readonly_lease.ReadOnlyLeaseFactory,
            "ReadOnlyProfileError": readonly_lease.ReadOnlyProfileError,
            "ReadOnlyScopeError": readonly_lease.ReadOnlyScopeError,
            "ReadOnlySecretError": readonly_lease.ReadOnlySecretError,
            "ReadOnlyTarget": readonly_lease.ReadOnlyTarget,
            "RedisReadOnlyFacade": readonly_nosql_clients.RedisReadOnlyFacade,
            "load_agent_model_snapshot": model_config.load_agent_model_snapshot,
        }
        for name, value in exported.items():
            self.assertIn(name, package.__all__)
            self.assertIs(getattr(package, name), value)

    def test_all_public_names_are_bound(self) -> None:
        import tools.data_center as package

        missing = [name for name in package.__all__ if not hasattr(package, name)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
