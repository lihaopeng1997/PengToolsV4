"""Lazy user-directory tree tests; every provider below is a fake adapter."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import tools.data_center.object_tree as object_tree_module
from tools.data_center.host_metadata import MetadataTarget
from tools.data_center.object_tree import (
    CleanupResult,
    ObjectTreeError,
    ObjectTreeService,
    TreeNode,
    TreeNodeDetails,
    TreePage,
    TreeTarget,
)
from tools.data_center.readonly_nosql_clients import NoSQLDirectoryEntry, NoSQLDirectoryPage


class _RelationalLease:
    def __init__(self, target: MetadataTarget, owner: "_RelationalHost") -> None:
        self.target = target
        self.dialect = "mysql" if target.provider.endswith("mysql") else "oracle"
        self.owner = owner

    def execute_readonly(self, request, **_kwargs):
        self.owner.calls.append((threading.get_ident(), request["sql"], tuple(request["parameters"])))
        if self.owner.block_query:
            self.owner.query_started.set()
            self.owner.query_release.wait(3)
        sql = request["sql"].casefold()
        offset = int(request["parameters"][-1]) if request["parameters"] else 0
        if "information_schema.schemata" in sql:
            rows = [("analytics",), ("sales db",)]
        elif "information_schema.tables" in sql:
            rows = [("order detail", "BASE TABLE"), ("summary", "VIEW")]
        elif "select distinct owner" in sql:
            rows = [("APP",), ("Quoted Owner",)]
        elif "select object_name, object_type" in sql:
            rows = [("ORDERS", "TABLE"), ("ORDER_VIEW", "VIEW")]
        else:
            rows = []
        return {"columns": (), "rows": rows[offset : offset + int(request["row_limit"])]}

    def close(self):
        self.owner.closed_threads.append(threading.get_ident())


class _RelationalFactory:
    def __init__(self, target: MetadataTarget, owner: "_RelationalHost") -> None:
        self.target = target
        self.owner = owner

    def __call__(self, connection_id, **_kwargs):
        del connection_id
        self.owner.factory_threads.append(threading.get_ident())
        return _RelationalLease(self.target, self.owner)


class _RelationalHost:
    def __init__(self, provider: str, mode: str, *, database: str = "", block_query: bool = False) -> None:
        self.provider = provider
        self.mode = mode
        self.database = database
        self.factory_threads: list[int] = []
        self.calls: list[tuple[int, str, tuple]] = []
        self.closed_threads: list[int] = []
        self.block_query = block_query
        self.query_started = threading.Event()
        self.query_release = threading.Event()

    def profile_snapshot(self, connection_id):
        return SimpleNamespace(
            connection_id=connection_id,
            profile_revision="rev-1",
            provider=self.provider,
            mode=self.mode,
            database=self.database,
        )

    def create_factory(self, connection_id, profile_revision, provider, mode, database_name, **_kwargs):
        target = MetadataTarget(connection_id, profile_revision, provider, mode, database_name, ())
        return _RelationalFactory(target, self)


class _DirectoryLease:
    _directory_metadata = True

    def __init__(self, host: "_DirectoryHost") -> None:
        self.host = host
        self.closed = False

    def _directory_children(self, parent_kind, **kwargs):
        thread_id = threading.get_ident()
        self.host.directory_threads.append(thread_id)
        self.host.calls.append((parent_kind, dict(kwargs)))
        if self.host.block_scan and parent_kind == "redis_root":
            self.host.started.set()
            self.host.release.wait(3)
        if parent_kind == "redis_root" and self.host.entries is not None:
            return NoSQLDirectoryPage(tuple(self.host.entries))
        if self.host.provider == "redis":
            if parent_kind == "redis_root":
                return NoSQLDirectoryPage(
                    (
                        NoSQLDirectoryEntry("same-label", "redis_key", b"tenant/\xff"),
                        NoSQLDirectoryEntry("same-label", "redis_key", b"tenant/\xfe"),
                        NoSQLDirectoryEntry("same-label", "redis_key", b"tenant/" + b"x" * 4097, "redis_key_identity_over_budget"),
                        NoSQLDirectoryEntry("visible", "redis_key", b"tenant/visible"),
                    ),
                    notices=("redis_key_identity_over_budget",),
                )
            return NoSQLDirectoryPage(())
        if self.host.provider == "mongodb":
            if parent_kind == "mongo_root":
                return NoSQLDirectoryPage((NoSQLDirectoryEntry("analytics", "mongo_database", "analytics"),))
            return NoSQLDirectoryPage((NoSQLDirectoryEntry("orders", "mongo_collection", "orders"),))
        raise AssertionError(parent_kind)

    def _directory_inspect_key(self, identity, **_kwargs):
        self.host.inspected.append((identity, threading.get_ident()))
        return {"value_type": "string", "ttl_seconds": -1}

    def execute(self, *_args, **_kwargs):
        self.host.agent_execute_calls += 1
        raise AssertionError("ObjectTree must not call the Agent route")

    def close(self):
        self.closed = True
        self.host.close_threads.append(threading.get_ident())


class _DirectoryFactory:
    def __init__(self, host: "_DirectoryHost") -> None:
        self.host = host

    def __call__(self, _connection_id):
        self.host.factory_threads.append(threading.get_ident())
        lease = _DirectoryLease(self.host)
        self.host.leases.append(lease)
        return lease

    def close(self):
        self.host.factory_close_threads.append(threading.get_ident())


class _DirectoryHost:
    def __init__(self, provider: str, mode: str, *, database_name: str = "", block_scan: bool = False) -> None:
        self.provider = provider
        self.mode = mode
        self.database_name = database_name
        self.block_scan = block_scan
        self.started = threading.Event()
        self.release = threading.Event()
        self.factory_threads: list[int] = []
        self.directory_threads: list[int] = []
        self.close_threads: list[int] = []
        self.factory_close_threads: list[int] = []
        self.inspected: list[tuple[bytes, int]] = []
        self.calls: list[tuple[str, dict]] = []
        self.leases: list[_DirectoryLease] = []
        self.agent_execute_calls = 0
        self.directory_factory_calls: list[int] = []
        self.entries: tuple[NoSQLDirectoryEntry, ...] | None = None

    def profile_snapshot(self, connection_id):
        return SimpleNamespace(
            connection_id=connection_id,
            profile_revision="rev-1",
            provider=self.provider,
            mode=self.mode,
            database_name=self.database_name,
        )

    def create_directory_factory(self, connection_id, **_kwargs):
        self.directory_factory_calls.append(threading.get_ident())
        return _DirectoryFactory(self)


class _ChangingSnapshotDirectoryHost(_DirectoryHost):
    def __init__(self, provider: str, mode: str, *, after_open: str) -> None:
        super().__init__(provider, mode)
        self.after_open = after_open
        self.snapshot_calls = 0

    def profile_snapshot(self, connection_id):
        self.snapshot_calls += 1
        if self.snapshot_calls == 2 and self.after_open == "unavailable":
            raise RuntimeError("sensitive profile diagnostic")
        snapshot = super().profile_snapshot(connection_id)
        if self.snapshot_calls == 2 and self.after_open == "mismatch":
            snapshot.profile_revision = "rev-2"
        return snapshot


class _UnmarkedDirectoryFactory(_DirectoryFactory):
    def __call__(self, connection_id):
        lease = super().__call__(connection_id)
        lease._directory_metadata = False
        return lease


class _UnmarkedDirectoryHost(_DirectoryHost):
    def create_directory_factory(self, connection_id, **_kwargs):
        self.directory_factory_calls.append(threading.get_ident())
        return _UnmarkedDirectoryFactory(self)


class ObjectTreeServiceTests(unittest.TestCase):
    def _page(self, service: ObjectTreeService, target: TreeTarget, node: TreeNode, **kwargs) -> TreePage:
        value = service.submit_children(target, node, **kwargs).future.result(timeout=3)
        self.assertIsInstance(value, TreePage)
        return value

    def test_relational_tree_uses_fixed_templates_for_mysql_oracle_and_both_oceanbase_modes(self):
        cases = (
            ("mysql", "mysql", "database", "table"),
            ("oracle", "oracle", "schema", "table"),
            ("oceanbase_oracle", "oracle", "schema", "table"),
            ("oceanbase_mysql", "mysql", "database", "table"),
        )
        for provider, mode, container_kind, leaf_kind in cases:
            with self.subTest(provider=provider):
                host = _RelationalHost(provider, mode, database="analytics")
                service = ObjectTreeService(relational_host=host, page_size=2)
                target = TreeTarget("conn-1", "rev-1", provider, mode=mode, database="analytics")
                root = service.root(target)
                self.assertEqual(host.factory_threads, [])  # Root issuance is zero-I/O.
                first = self._page(service, target, root, generation=1)
                self.assertEqual(first.nodes[0].kind, container_kind)
                container = first.nodes[0]
                second = self._page(service, target, container, generation=1)
                self.assertEqual(second.nodes[0].kind, leaf_kind)
                self.assertIn(second.nodes[0].name, {"order detail", "ORDERS"})
                self.assertEqual(len(host.calls), 2)
                owner_threads = {thread_id for thread_id, *_rest in host.calls}
                owner_threads.update(host.factory_threads)
                owner_threads.update(host.closed_threads)
                self.assertEqual(len(owner_threads), 1)
                service.close().result(timeout=3)

    def test_tree_target_canonicalizes_provider_mode_and_rejects_mismatch(self):
        ob = TreeTarget("conn-1", "rev-1", "ob", mode="oracle")
        self.assertEqual((ob.provider, ob.mode), ("oceanbase_oracle", "oracle"))
        with self.assertRaises(ValueError):
            TreeTarget("conn-1", "rev-1", "oceanbase_oracle", mode="mysql")
        with self.assertRaises(ValueError):
            TreeTarget("conn-1", "rev-1", "oracle", mode="mysql")
        redis = TreeTarget("redis-1", "rev-1", "redis")
        self.assertEqual(redis.mode, "standalone")

    def test_mysql_paging_cursor_refresh_and_generation_are_bound_to_issued_node(self):
        host = _RelationalHost("mysql", "mysql", database="analytics")
        service = ObjectTreeService(relational_host=host, page_size=1)
        target = TreeTarget("conn-1", "rev-1", "mysql", mode="mysql", database="analytics")
        root = service.root(target)
        first = self._page(service, target, root, generation=3)
        self.assertEqual(first.nodes[0].name, "analytics")
        self.assertIsNotNone(first.next_cursor)
        cached = self._page(service, target, root, generation=3)
        self.assertEqual(cached.cache_state, "cached")
        self.assertEqual(len(host.calls), 1)

        second = self._page(service, target, root, cursor=first.next_cursor, generation=3)
        self.assertEqual(second.nodes[0].name, "sales db")
        reused = service.submit_children(target, root, cursor=first.next_cursor, generation=3)
        with self.assertRaises(ObjectTreeError) as captured:
            reused.future.result(timeout=1)
        self.assertEqual(captured.exception.code, "STALE_CURSOR")

        stale_generation = service.submit_children(target, root, generation=2)
        with self.assertRaises(ObjectTreeError) as captured:
            stale_generation.future.result(timeout=1)
        self.assertEqual(captured.exception.code, "STALE_NODE")
        service.close().result(timeout=3)

    def test_signed_node_forgery_target_mismatch_expiry_and_refresh_invalidate_descendants(self):
        now = [10.0]
        host = _RelationalHost("mysql", "mysql", database="analytics")
        service = ObjectTreeService(relational_host=host, page_size=1, clock=lambda: now[0], node_ttl_seconds=5)
        target = TreeTarget("conn-1", "rev-1", "mysql", mode="mysql", database="analytics")
        root = service.root(target)
        page = self._page(service, target, root)
        child = page.nodes[0]

        forged = replace(child, node_ref="tree_node_not-issued")
        forged_request = service.submit_children(target, forged)
        with self.assertRaises(ObjectTreeError) as captured:
            forged_request.future.result(timeout=1)
        self.assertEqual(captured.exception.code, "STALE_NODE")
        other_target = TreeTarget("conn-2", "rev-1", "mysql", mode="mysql", database="analytics")
        wrong_scope = service.submit_children(other_target, child)
        with self.assertRaises(ObjectTreeError) as captured:
            wrong_scope.future.result(timeout=1)
        self.assertEqual(captured.exception.code, "SCOPE_DENIED")

        refreshed = self._page(service, target, root, refresh=True, generation=1)
        self.assertEqual(refreshed.refresh_epoch, page.refresh_epoch + 1)
        stale_child = service.submit_children(target, child)
        with self.assertRaises(ObjectTreeError) as captured:
            stale_child.future.result(timeout=1)
        self.assertEqual(captured.exception.code, "STALE_NODE")

        now[0] += 6
        expired_root = service.submit_children(target, root)
        with self.assertRaises(ObjectTreeError) as captured:
            expired_root.future.result(timeout=1)
        self.assertEqual(captured.exception.code, "STALE_NODE")
        service.close().result(timeout=3)

    def test_nosql_directory_keeps_binary_identity_private_and_surfaces_overbudget_notice(self):
        host = _DirectoryHost("redis", "cluster")
        service = ObjectTreeService(nosql_host=host)
        target = TreeTarget("redis-1", "rev-1", "redis", mode="cluster", redis_prefix="tenant/")
        root = service.root(target)
        page = self._page(service, target, root)
        self.assertEqual(len(page.nodes), 4)
        binary, second_binary, oversized, visible = page.nodes
        self.assertEqual(binary.name, second_binary.name)
        self.assertNotEqual(binary.node_ref, second_binary.node_ref)
        self.assertTrue(binary.actionable)
        self.assertTrue(second_binary.actionable)
        self.assertFalse(oversized.actionable)
        self.assertEqual(oversized.notice, "redis_key_identity_over_budget")
        self.assertIn("redis_key_identity_over_budget", page.notices)
        self.assertNotIn("tenant/\xff", repr(binary))
        self.assertNotIn("tenant/\xff", repr(page))

        details = service.inspect_node(target, binary).future.result(timeout=3)
        self.assertIsInstance(details, TreeNodeDetails)
        self.assertEqual(details.value_type, "string")
        self.assertEqual(host.inspected[0][0], b"tenant/\xff")
        self.assertEqual(host.inspected[0][1], host.factory_threads[0])
        second_details = service.inspect_node(target, second_binary).future.result(timeout=3)
        self.assertIsInstance(second_details, TreeNodeDetails)
        self.assertEqual(host.inspected[1][0], b"tenant/\xfe")
        self.assertEqual(host.agent_execute_calls, 0)
        self.assertTrue(visible.actionable)
        service.close().result(timeout=3)

    def test_post_open_profile_drift_or_unavailability_closes_lease_on_owner(self):
        for behavior, expected_code in (
            ("mismatch", "PROFILE_MISMATCH"),
            ("unavailable", "PROFILE_UNAVAILABLE"),
        ):
            with self.subTest(behavior=behavior):
                host = _ChangingSnapshotDirectoryHost("redis", "cluster", after_open=behavior)
                service = ObjectTreeService(nosql_host=host)
                target = TreeTarget("redis-1", "rev-1", "redis", mode="cluster", redis_prefix="tenant/")
                request = service.submit_children(target, service.root(target))
                with self.assertRaises(ObjectTreeError) as captured:
                    request.future.result(timeout=3)
                self.assertEqual(captured.exception.code, expected_code)
                self.assertNotIn("sensitive", captured.exception.message)
                self.assertEqual(host.calls, [])
                self.assertEqual(host.directory_threads, [])
                self.assertEqual(len(host.leases), 1)
                self.assertTrue(host.leases[0].closed)
                self.assertEqual(host.close_threads, host.factory_threads)

                cleanup = service.close()
                self.assertIs(service.close(), cleanup)
                self.assertEqual(cleanup.result(timeout=3), CleanupResult(True, 0))
                self.assertEqual(host.factory_close_threads, host.factory_threads)
                self.assertEqual(len(host.close_threads), 1)

    def test_unverified_directory_marker_closes_new_lease_before_directory_call(self):
        host = _UnmarkedDirectoryHost("redis", "cluster")
        service = ObjectTreeService(nosql_host=host)
        target = TreeTarget("redis-1", "rev-1", "redis", mode="cluster", redis_prefix="tenant/")
        request = service.submit_children(target, service.root(target))
        with self.assertRaises(ObjectTreeError) as captured:
            request.future.result(timeout=3)
        self.assertEqual(captured.exception.code, "SCOPE_DENIED")
        self.assertEqual(host.calls, [])
        self.assertEqual(host.directory_threads, [])
        self.assertEqual(len(host.leases), 1)
        self.assertTrue(host.leases[0].closed)
        self.assertEqual(host.close_threads, host.factory_threads)
        cleanup = service.close()
        self.assertIs(service.close(), cleanup)
        self.assertEqual(cleanup.result(timeout=3), CleanupResult(True, 0))
        self.assertEqual(host.factory_close_threads, host.factory_threads)
        self.assertEqual(len(host.close_threads), 1)

    def test_mongodb_database_and_collection_nodes_use_the_signed_parent_scope(self):
        host = _DirectoryHost("mongodb", "cluster", database_name="analytics")
        service = ObjectTreeService(nosql_host=host)
        target = TreeTarget("mongo-1", "rev-1", "mongodb", mode="cluster", database="analytics")
        root = service.root(target)
        databases = self._page(service, target, root)
        database = databases.nodes[0]
        self.assertEqual((database.kind, database.database), ("mongo_database", "analytics"))
        collections = self._page(service, target, database)
        self.assertEqual((collections.nodes[0].kind, collections.nodes[0].name), ("mongo_collection", "orders"))
        leaf = self._page(service, target, collections.nodes[0])
        self.assertEqual(leaf.nodes, ())
        self.assertEqual([kind for kind, _kwargs in host.calls], ["mongo_root", "mongo_database"])
        self.assertEqual(host.calls[1][1]["database"], "analytics")
        service.close().result(timeout=3)

    def test_dameng_reports_connector_gap_without_constructing_a_database_host(self):
        host = _RelationalHost("dameng", "dameng")
        service = ObjectTreeService(relational_host=host)
        target = TreeTarget("dm-1", "rev-1", "dameng", mode="dameng")
        page = self._page(service, target, service.root(target))
        self.assertEqual(page.nodes, ())
        self.assertTrue(page.capability_notice)
        self.assertIn("dameng_directory_unavailable_readonly_connector_not_wired", page.notices)
        self.assertEqual(host.factory_threads, [])
        service.close().result(timeout=3)

    def test_close_future_waits_for_a_blocked_owner_and_closes_on_its_worker(self):
        host = _DirectoryHost("redis", "cluster", block_scan=True)
        service = ObjectTreeService(nosql_host=host)
        target = TreeTarget("redis-1", "rev-1", "redis", mode="cluster", redis_prefix="tenant/")
        request = service.submit_children(target, service.root(target))
        self.assertTrue(host.started.wait(2))
        cleanup = service.close()
        self.assertFalse(cleanup.done())
        host.release.set()
        with self.assertRaises(ObjectTreeError) as captured:
            request.future.result(timeout=3)
        self.assertEqual(captured.exception.code, "CANCELLED")
        result = cleanup.result(timeout=3)
        self.assertEqual(result, CleanupResult(True, 1))
        self.assertEqual(host.close_threads[0], host.directory_threads[0])
        rejected = service.submit_children(target, TreeNode("x", "root", "root"))
        with self.assertRaises(ObjectTreeError):
            rejected.future.result(timeout=1)

    def test_close_future_also_waits_for_relational_metadata_worker_exit(self):
        host = _RelationalHost("mysql", "mysql", database="analytics", block_query=True)
        service = ObjectTreeService(relational_host=host)
        target = TreeTarget("conn-1", "rev-1", "mysql", mode="mysql", database="analytics")
        request = service.submit_children(target, service.root(target))
        self.assertTrue(host.query_started.wait(2))
        cleanup = service.close()
        self.assertFalse(cleanup.done())
        host.query_release.set()
        with self.assertRaises(ObjectTreeError) as captured:
            request.future.result(timeout=3)
        self.assertEqual(captured.exception.code, "CANCELLED")
        self.assertEqual(cleanup.result(timeout=3), CleanupResult(True, 1))
        self.assertEqual(host.closed_threads[0], host.calls[0][0])

    def test_late_page_is_dropped_after_newer_generation_refresh(self):
        host = _DirectoryHost("redis", "cluster", block_scan=True)
        service = ObjectTreeService(nosql_host=host)
        target = TreeTarget("redis-1", "rev-1", "redis", mode="cluster", redis_prefix="tenant/")
        root = service.root(target)
        older = service.submit_children(target, root, generation=1)
        self.assertTrue(host.started.wait(2))
        newer = service.submit_children(target, root, generation=2, refresh=True)
        host.release.set()
        old_page = older.future.result(timeout=3)
        new_page = newer.future.result(timeout=3)
        self.assertEqual(old_page.cache_state, "stale")
        self.assertEqual(old_page.nodes, ())
        self.assertEqual(new_page.generation, 2)
        self.assertEqual(new_page.cache_state, "fresh")
        self.assertEqual(len(host.calls), 2)
        service.close().result(timeout=3)

    def test_node_and_page_cache_registry_limits_never_return_evicted_refs(self):
        host = _DirectoryHost("redis", "cluster")
        host.entries = tuple(
            NoSQLDirectoryEntry(f"key-{index}", "redis_key", f"tenant/key-{index}".encode())
            for index in range(5)
        )
        with (
            patch.object(object_tree_module, "MAX_NODE_ENTRIES", 3),
            patch.object(object_tree_module, "MAX_NODE_REGISTRY_BYTES", 500),
            patch.object(object_tree_module, "MAX_PAGE_CACHE_ENTRIES", 1),
            patch.object(object_tree_module, "MAX_PAGE_CACHE_BYTES", 450),
        ):
            service = ObjectTreeService(nosql_host=host, page_size=10)
            target = TreeTarget("redis-1", "rev-1", "redis", mode="cluster", redis_prefix="tenant/")
            root = service.root(target)
            page = self._page(service, target, root)
            self.assertLessEqual(len(page.nodes), 1)
            self.assertIn("tree_node_registry_budget_exhausted", page.notices)
            with service._lock:
                self.assertLessEqual(len(service._nodes), 3)
                self.assertLessEqual(service._node_bytes, 500)
                self.assertLessEqual(len(service._pages), 1)
                self.assertLessEqual(service._page_bytes, 450)
            child_page = self._page(service, target, page.nodes[0])
            self.assertEqual(child_page.nodes, ())
            with service._lock:
                self.assertLessEqual(len(service._pages), 1)
                self.assertLessEqual(service._page_bytes, 450)
            refreshed = self._page(service, target, root, refresh=True, generation=1)
            self.assertTrue(refreshed.nodes)
            self.assertEqual(len(host.calls), 2)
            service.close().result(timeout=3)


if __name__ == "__main__":
    unittest.main()
