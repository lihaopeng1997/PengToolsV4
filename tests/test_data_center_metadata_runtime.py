"""Focused closed-loop tests for host metadata and runtime composition."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import unittest

from tools.data_center.budget import Budget, BudgetLimits
from tools.data_center.contracts import AgentStatus, AgentTurn, CancellationToken, RunContext, ToolCall, ToolResult
from tools.data_center.host_metadata import MetadataHost, MetadataTarget
from tools.data_center.host_runtime import (
    DataCenterAgentRuntime,
    RuntimeConfigurationError,
    ScopedResultProjector,
    TargetSQLPolicy,
)
from tools.data_center.readonly_lease import ReadOnlyLeaseFactory, ReadOnlyTarget


def _context(*, intent: str = "query", schemas: tuple[str, ...] = ("APP",)) -> RunContext:
    return RunContext(
        run_id="run-1",
        tab_id="tab-1",
        model_config_id="model-1",
        connection_id="conn-1",
        database="DB1",
        schema_allowlist=schemas,
        profile_revision="rev-1",
        intent=intent,
        policy_version="dc-02",
    )


class _MetadataLease:
    def __init__(self, target: MetadataTarget, rows: list[tuple] | None = None) -> None:
        self.target = target
        self.dialect = "mysql" if target.provider.endswith("mysql") else "oracle"
        self.rows = rows or []
        self.calls: list[tuple[str, tuple]] = []
        self.thread_ids: list[int] = []
        self.closed = False

    def execute_readonly(self, request, **_kwargs):
        self.thread_ids.append(threading.get_ident())
        self.calls.append((request["sql"], tuple(request.get("parameters", ()))))
        return {"columns": (), "rows": list(self.rows)}

    def close(self) -> None:
        self.closed = True


class _MetadataFactory:
    def __init__(self, target: MetadataTarget, rows: list[tuple] | None = None) -> None:
        self.target = target
        self.rows = rows or []
        self.calls = 0
        self.leases: list[_MetadataLease] = []

    def __call__(self, connection_id: str, **_kwargs):
        self.calls += 1
        lease = _MetadataLease(self.target, self.rows)
        self.leases.append(lease)
        return lease


class _OracleCursor:
    def __init__(self, connection: "_OracleConnection") -> None:
        self.connection = connection
        self.description = ()
        self.rows: list[tuple] = []

    def execute(self, sql, parameters=None):
        self.connection.events.append(("execute", str(sql), parameters))
        if "all_objects" in str(sql):
            self.description = (("OWNER",), ("OBJECT_NAME",), ("OBJECT_TYPE",), ("SUBOBJECT_NAME",))
            self.rows = [("APP", "USERS", "TABLE", None)]
        elif "all_tab_columns" in str(sql):
            self.description = (("OWNER",), ("TABLE_NAME",), ("COLUMN_NAME",), ("DATA_TYPE",), ("DATA_LENGTH",), ("NULLABLE",), ("COLUMN_ID",), ("COMMENTS",))
            self.rows = [("APP", "USERS", "ID", "NUMBER", 10, "Y", 1, None)]
        else:
            self.rows = []

    def fetchmany(self, size):
        self.connection.events.append(("fetchmany", size))
        return self.rows

    def close(self) -> None:
        self.connection.events.append(("cursor_close",))


class _OracleConnection:
    def __init__(self) -> None:
        self.autocommit = True
        self.call_timeout = 0
        self.events: list[tuple] = []
        self.closed = False

    def cursor(self):
        self.events.append(("cursor",))
        return _OracleCursor(self)

    def rollback(self):
        self.events.append(("rollback",))

    def close(self):
        self.events.append(("close",))
        self.closed = True


class _RedisLease:
    def __init__(self, target: MetadataTarget) -> None:
        self.target = target
        self.calls: list[tuple[dict, int]] = []
        self.closed = False

    def execute(self, request, **_kwargs):
        self.calls.append((dict(request), threading.get_ident()))
        if request["operation"] == "SCAN":
            if str(request.get("cursor", "0")) == "1":
                return ToolResult(ok=True, data={"keys": ["orders:2"], "next_cursor": 0})
            return ToolResult(ok=True, data={"keys": ["orders:1"], "next_cursor": "1"})
        return ToolResult(ok=True, data={"value": "string"})

    def close(self):
        self.closed = True


class _RedisFactory:
    def __init__(self, target: MetadataTarget) -> None:
        self.target = target
        self.calls = 0
        self.lease = _RedisLease(target)

    def __call__(self, connection_id: str, **_kwargs):
        self.calls += 1
        return self.lease


class MetadataHostTests(unittest.TestCase):
    def test_user_directory_pages_are_host_only_and_do_not_register_agent_tool(self) -> None:
        target = MetadataTarget("conn-1", "rev-1", "mysql", mode="mysql")
        factory = _MetadataFactory(target, [("app",), ("test",)])
        host = MetadataHost(target, lease_factory=factory, max_page_size=2)
        page = host.directory_children("root", page_size=1)
        self.assertTrue(page.ok)
        self.assertEqual(page.data["entries"], [{"name": "app", "kind": "database"}])
        self.assertEqual(page.data["next_offset"], 1)
        self.assertEqual(factory.calls, 1)

        denied = host.execute({"id": "directory-1", "name": "directory_children", "arguments": {"parent_kind": "root"}})
        self.assertFalse(denied.ok)
        self.assertEqual(denied.code, "ARGUMENT_INVALID")
        self.assertEqual(factory.calls, 1)
        host.close()

    def test_directory_parent_identifiers_keep_exact_metadata_name(self) -> None:
        target = MetadataTarget("conn-1", "rev-1", "mysql", mode="mysql")
        factory = _MetadataFactory(target, [("orders", "BASE TABLE")])
        host = MetadataHost(target, lease_factory=factory, max_page_size=2)
        result = host.directory_children("database", database="  sales db  ", page_size=1)
        self.assertTrue(result.ok, result)
        self.assertEqual(factory.leases[0].calls[0][1], ("  sales db  ", 2, 0))
        host.close()

    def test_oracle_catalog_uses_real_readonly_lease_and_numeric_binds(self) -> None:
        connection = _OracleConnection()
        lease_target = ReadOnlyTarget("conn-1", "rev-1", "oracle", dialect="oracle")
        profile = {
            "connection_id": "conn-1",
            "profile_revision": "rev-1",
            "provider": "oracle",
            "mode": "oracle",
            "dialect": "oracle",
            "database": "DB1",
            "schema_allowlist": ["APP"],
        }
        factory = ReadOnlyLeaseFactory(
            lease_target,
            profile_loader=lambda *_args, **_kwargs: profile,
            secret_loader=lambda *_args, **_kwargs: "secret-value",
            connector=lambda *_args, **_kwargs: connection,
        )
        host = MetadataHost(
            MetadataTarget("conn-1", "rev-1", "oracle", mode="oracle", database="DB1", schema_allowlist=("APP",)),
            lease_factory=factory,
        )
        result = host.search_objects({"keyword": "USER"}, query_id="search-1")
        host.close()
        self.assertTrue(result.ok, result)
        self.assertEqual(result.data["objects"][0]["name"], "USERS")
        catalog_call = next(event for event in connection.events if event[0] == "execute" and "all_objects" in event[1])
        self.assertIn(":1", catalog_call[1])
        self.assertEqual(catalog_call[2], ("APP", "%USER%", "TABLE", "VIEW", 21, 0))
        self.assertTrue(connection.closed)
        self.assertNotIn("secret-value", repr(result))

    def test_real_lease_accepts_only_closed_metadata_template_ids(self) -> None:
        connection = _OracleConnection()
        lease_target = ReadOnlyTarget("conn-1", "rev-1", "oracle", dialect="oracle")
        factory = ReadOnlyLeaseFactory(
            lease_target,
            profile_loader=lambda *_args, **_kwargs: {
                "connection_id": "conn-1", "profile_revision": "rev-1", "provider": "oracle",
                "mode": "oracle", "dialect": "oracle",
            },
            secret_loader=lambda *_args, **_kwargs: "secret-value",
            connector=lambda *_args, **_kwargs: connection,
        )
        lease = factory("conn-1", session_id="metadata", generation=1)
        with self.assertRaises(Exception) as captured:
            lease.execute_metadata("oracle:arbitrary", ("SELECT injected",))
        self.assertEqual(getattr(captured.exception, "code", ""), "METADATA_TEMPLATE_UNAVAILABLE")
        self.assertFalse(any("all_objects" in event[1] for event in connection.events if event[0] == "execute"))
        lease.close()
        self.assertTrue(connection.closed)

    def test_real_lease_cancellation_and_deadline_stop_before_catalog_cursor(self) -> None:
        connection = _OracleConnection()
        lease_target = ReadOnlyTarget("conn-1", "rev-1", "oracle", dialect="oracle")
        factory = ReadOnlyLeaseFactory(
            lease_target,
            profile_loader=lambda *_args, **_kwargs: {
                "connection_id": "conn-1", "profile_revision": "rev-1", "provider": "oracle",
                "mode": "oracle", "dialect": "oracle",
            },
            secret_loader=lambda *_args, **_kwargs: "secret-value",
            connector=lambda *_args, **_kwargs: connection,
        )
        lease = factory("conn-1", session_id="metadata", generation=1)

        class Cancelled:
            def is_cancelled(self):
                return True

        with self.assertRaises(Exception):
            lease.execute_metadata("oracle:search", (), cancellation=Cancelled())
        with self.assertRaises(Exception):
            lease.execute_metadata("oracle:search", (), deadline=0.0)
        self.assertFalse(any("all_objects" in event[1] for event in connection.events if event[0] == "execute"))
        lease.close()
        self.assertTrue(connection.closed)

    def test_mysql_and_oceanbase_templates_are_fixed_and_parameterized(self) -> None:
        for provider, mode in (("mysql", ""), ("oceanbase_mysql", "mysql")):
            with self.subTest(provider=provider):
                target = MetadataTarget("conn-1", "rev-1", provider, mode=mode, database="DB1", schema_allowlist=("APP",))
                factory = _MetadataFactory(target, [("APP", "USERS", "BASE TABLE", "comment")])
                host = MetadataHost(target, lease_factory=factory)
                result = host.search_objects({"keyword": "USER"}, query_id="search")
                host.close()
                self.assertTrue(result.ok, result)
                sql, params = factory.leases[0].calls[0]
                self.assertIn("information_schema.TABLES", sql)
                self.assertIn("%s", sql)
                self.assertEqual(params[:4], ("APP", "%USER%", "BASE TABLE", "VIEW"))
                self.assertNotIn("USER", sql)

    def test_invalid_or_stale_metadata_scope_fails_before_factory(self) -> None:
        with self.assertRaises(ValueError):
            MetadataTarget("conn-1", "rev-1", "dameng", database="DB1", schema_allowlist=("APP",))
        target = MetadataTarget("conn-1", "rev-1", "oracle", mode="oracle", database="DB1", schema_allowlist=("APP",))
        stale_target = MetadataTarget("conn-1", "rev-old", "oracle", mode="oracle", database="DB1", schema_allowlist=("APP",))
        factory = _MetadataFactory(stale_target)
        host = MetadataHost(target, lease_factory=factory)
        self.assertEqual(host.search_objects({"unexpected": 1}).code, "ARGUMENT_INVALID")
        self.assertEqual(factory.calls, 0)
        stale = host.search_objects({"keyword": "x"})
        host.close()
        self.assertFalse(stale.ok)
        self.assertEqual(stale.code, "SCOPE_DENIED")
        self.assertEqual(factory.calls, 0)

    def test_profile_revision_checked_after_catalog_result_and_lease_closed(self) -> None:
        target = MetadataTarget("conn-1", "rev-1", "oracle", mode="oracle", database="DB1", schema_allowlist=("APP",))
        factory = _MetadataFactory(target, [("APP", "USERS", "TABLE", None)])
        snapshots = [target, {"connection_id": "conn-1", "profile_revision": "rev-2", "provider": "oracle", "mode": "oracle", "database": "DB1", "schemas": ["APP"]}]

        def snapshot(*_args, **_kwargs):
            return snapshots.pop(0)

        host = MetadataHost(target, lease_factory=factory, profile_snapshot=snapshot)
        result = host.search_objects({"keyword": "x"}, query_id="q")
        host.close()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "PROFILE_MISMATCH")
        self.assertTrue(factory.leases[0].closed)

    def test_redis_cursor_keeps_one_worker_lease_across_pages(self) -> None:
        target = MetadataTarget("conn-1", "rev-1", "redis", database="db")
        factory = _RedisFactory(target)
        host = MetadataHost(target, lease_factory=factory, max_page_size=1)
        first = host.search_objects({"keyword": "orders"}, query_id="s1")
        token = first.data["next_page_token"]
        second = host.search_objects({"keyword": "orders", "page_token": token}, query_id="s2")
        host.close()
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(factory.calls, 1)
        self.assertTrue(factory.lease.closed)
        self.assertEqual(len({thread_id for _request, thread_id in factory.lease.calls}), 1)

    def test_mongo_search_uses_only_host_allowlist_without_network(self) -> None:
        class Target:
            connection_id = "conn-1"
            profile_revision = "rev-1"
            provider = "mongodb"
            mode = ""
            allowed_collection_ids = ("orders", "audit")

        target = MetadataTarget("conn-1", "rev-1", "mongodb", database="db")
        factory = _MetadataFactory(target)
        factory.target = Target()
        host = MetadataHost(target, lease_factory=factory)
        result = host.search_objects({"keyword": "orders"}, query_id="m")
        host.close()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["objects"][0]["name"], "orders")
        self.assertEqual(factory.calls, 0)


class TargetPolicyTests(unittest.TestCase):
    def test_physical_tables_require_explicit_bound_schema_and_cte_bodies_are_checked(self) -> None:
        policy = TargetSQLPolicy(provider="oracle", database="DB1", schemas=("APP",))
        self.assertTrue(policy.validate("SELECT * FROM APP.USERS", "oracle").allowed)
        self.assertFalse(policy.validate("SELECT * FROM USERS", "oracle").allowed)
        self.assertFalse(policy.validate("SELECT * FROM OTHER.USERS", "oracle").allowed)
        self.assertTrue(policy.validate("WITH x AS (SELECT * FROM APP.USERS) SELECT * FROM x", "oracle").allowed)
        self.assertFalse(policy.validate("WITH x AS (SELECT * FROM OTHER.USERS) SELECT * FROM x", "oracle").allowed)
        self.assertTrue(policy.validate("WITH x AS (SELECT 1) SELECT * FROM x", "oracle").allowed)
        self.assertTrue(policy.validate("SELECT 1", "oracle").allowed)
        self.assertFalse(policy.validate('SELECT * FROM "app"."USERS"', "oracle").allowed)
        self.assertFalse(policy.validate("SELECT * FROM APP.USERS@DBLINK", "oracle").allowed)

    def test_projection_removes_secret_scope_and_untrusted_comments(self) -> None:
        projector = ScopedResultProjector(max_result_bytes=16 * 1024)
        result = projector.project(
            ToolResult(
                ok=True,
                data={
                    "password": "do-not-show",
                    "comment": "model instruction",
                    "field_descriptor": {"name": "api_token", "value": "secret"},
                },
                limits={"row_limit": 1, "internal_detail": "hidden"},
                scope={"connection_id": "c", "secret": "hidden", "schemas": ["APP"]},
            )
        )
        self.assertNotIn("password", result.data)
        self.assertEqual(result.data["[redacted]"], {"type": "redacted"})
        self.assertEqual(result.data["comment"], {"type": "untrusted_text", "omitted": True})
        self.assertEqual(result.data["field_descriptor"]["value"], {"type": "redacted"})
        self.assertNotIn("internal_detail", result.limits)
        self.assertNotIn("secret", result.scope)


class RuntimeTests(unittest.TestCase):
    def test_draft_intent_has_zero_database_construction_and_model_identity_is_bound(self) -> None:
        class Model:
            model_config_id = "model-1"

            def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
                return AgentTurn(text="draft", finish_reason="stop")

        runtime = DataCenterAgentRuntime(_context(intent="draft"), model=Model())
        result = runtime.registry.execute(ToolCall("draft-search", "search_objects", {"keyword": "x"}), context=runtime.context, cancellation=CancellationToken())
        runtime.close()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DRAFT_NO_DATABASE")
        with self.assertRaises(RuntimeConfigurationError):
            DataCenterAgentRuntime(_context(intent="draft"), model=Model(), model_config_id="other")

    def test_runtime_model_search_describe_query_final_is_scoped_and_grounded(self) -> None:
        target = MetadataTarget("conn-1", "rev-1", "oracle", mode="oracle", database="DB1", schema_allowlist=("APP",))

        class Metadata:
            def execute(self, call, *, context, cancellation, **_kwargs):  # noqa: ARG002
                if call.name == "search_objects":
                    return ToolResult(ok=True, data={"objects": [{"object_id": "obj-1", "name": "USERS", "kind": "table", "schema": "APP"}]}, evidence_id="metadata:s", query_id=call.call_id, scope=target.scope)
                return ToolResult(ok=True, data={"object_id": "obj-1", "name": "USERS", "fields": [{"name": "ID", "type": "NUMBER"}]}, evidence_id="metadata:d", query_id=call.call_id, scope=target.scope)

        class Query:
            host_only = True
            verified_readonly = True

            def __init__(self):
                self.calls = []
                self.tracker = None

            def __call__(self, call, *, context, cancellation):  # noqa: ARG002
                from concurrent.futures import Future

                self.calls.append(call)
                # 生产查询工具会把执行它的 Future 登记为查询所有者；这里登记一个已完成的
                future = Future()
                future.query_id = call.call_id
                future.session_id = context.tab_id
                future.generation = 1
                future.set_result(None)
                self.tracker.register_query_owner(
                    context, call.call_id, session_id=context.tab_id, generation=1, future=future,
                )
                return ToolResult(ok=True, data={"columns": [{"name": "COUNT"}], "rows": [(12,)]}, evidence_id="query:e", query_id=call.call_id, scope=target.scope)

        class Model:
            model_config_id = "model-1"

            def __init__(self):
                self.requests = []

            def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
                self.requests.append(request)
                index = len(self.requests)
                if index == 1:
                    return AgentTurn(tool_calls=(ToolCall("s", "search_objects", {"keyword": "USER"}),), finish_reason="tool_calls")
                if index == 2:
                    payload = json.loads(request.messages[-1]["content"])
                    object_id = payload["data"]["objects"][0]["object_id"]
                    return AgentTurn(tool_calls=(ToolCall("d", "describe_object", {"object_id": object_id}),), finish_reason="tool_calls")
                if index == 3:
                    return AgentTurn(tool_calls=(ToolCall("q", "query_readonly", {"sql": "SELECT COUNT(*) FROM APP.USERS"}),), finish_reason="tool_calls")
                return AgentTurn(text="12 rows", finish_reason="stop")

        query = Query()
        model = Model()
        runtime = DataCenterAgentRuntime(_context(), provider="oracle", model=model, metadata_host=Metadata(), query_tool=query)
        query.tracker = runtime.query_owner_tracker
        result = runtime.run("count users")
        final_events = [event for event in result.events if event.event_type == "final"]
        runtime.close()
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(query.calls), 1)
        self.assertTrue(final_events)
        payload = final_events[-1].payload
        self.assertTrue(payload["verified"])
        self.assertEqual(payload["query_ids"], ["q"])
        self.assertIn("metadata:s", payload["evidence_ids"])
        self.assertIn("metadata:d", payload["evidence_ids"])

    def test_runtime_query_scope_rejection_makes_zero_query_calls(self) -> None:
        class Metadata:
            def execute(self, call, *, context, cancellation, **_kwargs):  # noqa: ARG002
                return ToolResult(ok=True, data={"objects": []}, query_id=call.call_id)

        class Query:
            host_only = True
            verified_readonly = True
            calls = 0

            def __call__(self, call, *, context, cancellation):  # noqa: ARG002
                self.calls += 1
                return ToolResult(ok=True, data={"columns": [], "rows": []}, query_id=call.call_id)

        query = Query()
        runtime = DataCenterAgentRuntime(_context(), provider="oracle", model=object(), metadata_host=Metadata(), query_tool=query)
        result = runtime.registry.execute(ToolCall("q", "query_readonly", {"sql": "SELECT * FROM OTHER.USERS"}), context=runtime.context, cancellation=CancellationToken())
        runtime.close()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "SCOPE_DENIED")
        self.assertEqual(query.calls, 0)

    def test_runtime_nosql_factory_and_cursor_lease_run_on_one_owned_worker(self) -> None:
        target = MetadataTarget("conn-1", "rev-1", "redis", database="DB1")

        class Lease:
            def __init__(self):
                self.target = target
                self.calls = []
                self.closed = False

            def execute(self, request, **_kwargs):
                self.calls.append((dict(request), threading.get_ident()))
                return ToolResult(ok=True, data={"value": "bounded"}, query_id="from-driver")

            def close(self):
                self.closed = True

        class Factory:
            def __init__(self):
                self.calls = []
                self.lease = Lease()

            def __call__(self, connection_id, **_kwargs):
                self.calls.append((connection_id, threading.get_ident()))
                return self.lease

        factory = Factory()
        runtime = DataCenterAgentRuntime(_context(), provider="redis", model=object(), nosql_factory=factory)
        caller_thread = threading.get_ident()
        first = runtime.registry.execute(ToolCall("r1", "redis_read", {"operation": "GET", "key": "orders:1"}), context=runtime.context, cancellation=CancellationToken())
        second = runtime.registry.execute(ToolCall("r2", "redis_read", {"operation": "TTL", "key": "orders:1"}), context=runtime.context, cancellation=CancellationToken())
        runtime.close()
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(len(factory.calls), 1)
        self.assertNotEqual(factory.calls[0][1], caller_thread)
        self.assertEqual(len({thread_id for _request, thread_id in factory.lease.calls}), 1)
        self.assertTrue(factory.lease.closed)

    def test_run_budget_deadline_reaches_runtime_before_metadata_io(self) -> None:
        class Metadata:
            calls = 0
            deadline = None

            def execute(self, call, *, context, cancellation, **_kwargs):  # noqa: ARG002
                self.calls += 1
                self.deadline = getattr(cancellation, "deadline", None)
                return ToolResult(ok=True, data={"objects": []}, query_id=call.call_id)

        class Model:
            model_config_id = "model-1"

            def complete_turn(self, request, *, cancellation, on_delta):  # noqa: ARG002
                if len(getattr(self, "turns", [])) == 0:
                    self.turns = [1]
                    return AgentTurn(tool_calls=(ToolCall("s", "search_objects", {"keyword": "x"}),), finish_reason="tool_calls")
                return AgentTurn(text="done", finish_reason="stop")

        metadata = Metadata()
        runtime = DataCenterAgentRuntime(_context(), provider="oracle", model=Model(), metadata_host=metadata)
        clock_value = [0.0]
        budget = Budget(BudgetLimits(max_duration_s=10.0), clock=lambda: clock_value[0])
        clock_value[0] = 6.0
        result = runtime.run("search", budget=budget)
        runtime.close()
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(metadata.calls, 1)
        self.assertIsNotNone(metadata.deadline)
        self.assertLess(metadata.deadline - time.monotonic(), 5.0)
        self.assertGreater(metadata.deadline - time.monotonic(), 2.0)


class ImportBoundaryTests(unittest.TestCase):
    def test_metadata_and_runtime_import_without_driver_qt_or_config_modules(self) -> None:
        script = """
import builtins
blocked = {'oracledb', 'pymysql', 'pyodbc', 'dmPython', 'PyQt5', 'PyQt6', 'config', 'tools.db_connect'}
real = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in blocked or any(name.startswith(item + '.') for item in blocked):
        raise AssertionError(name)
    return real(name, *args, **kwargs)
builtins.__import__ = guarded
import tools.data_center.host_metadata
import tools.data_center.host_runtime
import tools.data_center.object_tree
print('ok')
"""
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
