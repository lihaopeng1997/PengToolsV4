"""Qt request-correlation tests using injected futures and service fakes."""

from __future__ import annotations

from concurrent.futures import Future
import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThread, Qt, pyqtSlot
from PyQt6.QtWidgets import QApplication

from tools.data_center.object_tree import (
    CleanupResult,
    ObjectTreeError,
    ObjectTreeRequest,
    TreeNode,
    TreeNodeDetails,
    TreePage,
    TreeTarget,
)
from ui.data_center.object_tree_controller import (
    ObjectTreeController,
    TreeControllerCleanupResult,
    TreeControllerFailure,
)


class _ControlledFuture(Future):
    """A deterministic future whose cooperative cancellation stays pending."""

    def __init__(self):
        super().__init__()
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True
        return False


class _CancellationToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _FakeRequest:
    def __init__(self, request_id):
        self.request_id = request_id
        self.future = _ControlledFuture()
        self._token = _CancellationToken()

    def cancel(self):
        self._token.cancel()
        self.future.cancel()
        return False


class _FakeService:
    def __init__(self):
        self.root_calls = []
        self.children_calls = []
        self.inspect_calls = []
        self.invalidations = []
        self.requests = []
        self.close_calls = 0

    def root(self, target):
        self.root_calls.append((target, QThread.currentThread()))
        return TreeNode(
            f"root-ref-{len(self.root_calls)}",
            "root",
            "Connection",
            database=target.database,
        )

    def submit_children(self, target, node, *, cursor=None, refresh=False, generation=0):
        request = _FakeRequest(f"tree-request-{len(self.requests) + 1}")
        self.children_calls.append(
            {
                "target": target,
                "node": node,
                "cursor": cursor,
                "refresh": refresh,
                "generation": generation,
                "request": request,
            }
        )
        self.requests.append(request)
        return ObjectTreeRequest(request.request_id, request.future, request._token)

    def inspect_node(self, target, node, *, generation=0):
        request = _FakeRequest(f"tree-request-{len(self.requests) + 1}")
        self.inspect_calls.append((target, node, generation, request))
        self.requests.append(request)
        return ObjectTreeRequest(request.request_id, request.future, request._token)

    def invalidate(self, target, node=None):
        self.invalidations.append((target, node))

    def close(self):
        self.close_calls += 1
        raise AssertionError("controller must use its injected composition cleanup")


class _Recorder(QObject):
    def __init__(self):
        super().__init__()
        self.targets = []
        self.roots = []
        self.pages = []
        self.details = []
        self.failures = []
        self.cleanups = []
        self.gui_thread_flags = []

    def _record_thread(self):
        self.gui_thread_flags.append(QThread.currentThread() == self.thread())

    @pyqtSlot(object)
    def on_target(self, value):
        self._record_thread()
        self.targets.append(value)

    @pyqtSlot(object, object, int)
    def on_root(self, target, root, generation):
        self._record_thread()
        self.roots.append((target, root, generation))

    @pyqtSlot(object)
    def on_page(self, value):
        self._record_thread()
        self.pages.append(value)

    @pyqtSlot(object)
    def on_details(self, value):
        self._record_thread()
        self.details.append(value)

    @pyqtSlot(object)
    def on_failure(self, value):
        self._record_thread()
        self.failures.append(value)

    @pyqtSlot(object)
    def on_cleanup(self, value):
        self._record_thread()
        self.cleanups.append(value)


class ObjectTreeControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = _FakeService()
        self.capture_futures = []
        self.capture_calls = []
        self.cleanup_future = _ControlledFuture()
        self.cleanup_calls = []
        self.recorder = _Recorder()

        def target_capture(connection_id, *, database_scope="", redis_prefix=None):
            self.capture_calls.append((connection_id, database_scope, redis_prefix))
            future = _ControlledFuture()
            self.capture_futures.append(future)
            return future

        def cleanup():
            self.cleanup_calls.append("called")
            return self.cleanup_future

        self.controller = ObjectTreeController(
            service=self.service,
            target_capture=target_capture,
            cleanup=cleanup,
        )
        self.controller.target_changed.connect(self.recorder.on_target, Qt.ConnectionType.QueuedConnection)
        self.controller.root_ready.connect(self.recorder.on_root, Qt.ConnectionType.QueuedConnection)
        self.controller.page_ready.connect(self.recorder.on_page, Qt.ConnectionType.QueuedConnection)
        self.controller.details_ready.connect(self.recorder.on_details, Qt.ConnectionType.QueuedConnection)
        self.controller.request_failed.connect(self.recorder.on_failure, Qt.ConnectionType.QueuedConnection)
        self.controller.cleanup_finished.connect(self.recorder.on_cleanup, Qt.ConnectionType.QueuedConnection)

    def tearDown(self):
        from PyQt6 import sip

        if not sip.isdeleted(self.controller):
            self.controller.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _target(self, connection_id="conn-1", *, database="analytics", provider="mysql"):
        return TreeTarget(
            connection_id,
            f"revision-{connection_id}",
            provider,
            mode="mysql" if provider == "mysql" else "cluster",
            database=database,
            redis_prefix="tenant/" if provider == "redis" else "",
        )

    def _page(self, target, parent, generation, name, *, cursor=None, cache_state="fresh"):
        return TreePage(
            target=target,
            parent_ref=parent.node_ref,
            nodes=(TreeNode(f"{name}-ref", "schema", name, parent_ref=parent.node_ref),),
            next_cursor=cursor,
            generation=generation,
            refresh_epoch=0,
            loaded_at=1.0,
            cache_state=cache_state,
        )

    def _pump_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                self.app.processEvents()
                return True
            time.sleep(0.002)
        self.app.processEvents()
        return bool(predicate())

    def _complete_from_worker(self, future, *, result=None, error=None):
        def complete():
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

        worker = threading.Thread(target=complete)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self._pump_until(future.done))

    def _capture_and_wait(self, target):
        capture_id = self.controller.capture_target(
            target.connection_id,
            database_scope=target.database,
            redis_prefix=target.redis_prefix if target.provider == "redis" else None,
        )
        self._complete_from_worker(self.capture_futures[-1], result=target)
        self.assertTrue(self._pump_until(lambda: self.controller.current_target == target))
        self.assertTrue(self.recorder.roots)
        return self.recorder.roots[-1][1]

    def test_capture_uses_exact_target_and_delivers_root_on_qt_thread(self):
        target = self._target(provider="redis", database="")

        capture_id = self.controller.capture_target(
            target.connection_id,
            database_scope="",
            redis_prefix="",
        )
        self.assertTrue(capture_id.startswith("capture-"))
        self.assertEqual(self.capture_calls, [("conn-1", "", "")])
        self._complete_from_worker(self.capture_futures[-1], result=target)

        self.assertTrue(self._pump_until(lambda: len(self.recorder.roots) == 1))
        self.assertEqual(self.recorder.targets, [target])
        self.assertEqual(self.recorder.roots[0][0], target)
        self.assertEqual(self.recorder.roots[0][1].kind, "root")
        self.assertEqual(self.service.root_calls[0][0], target)
        self.assertEqual(self.service.root_calls[0][1], self.app.thread())
        self.assertTrue(all(self.recorder.gui_thread_flags))

    def test_older_capture_id_cannot_replace_new_target_and_keeps_its_future(self):
        first = self._target("conn-old")
        second = self._target("conn-new", database="reporting")
        first_id = self.controller.capture_target(first.connection_id, database_scope=first.database)
        first_future = self.capture_futures[0]
        second_id = self.controller.capture_target(second.connection_id, database_scope=second.database)
        second_future = self.capture_futures[1]

        self.assertNotEqual(first_id, second_id)
        self.assertTrue(first_future.cancel_requested)
        self._complete_from_worker(second_future, result=second)
        self.assertTrue(self._pump_until(lambda: self.controller.current_target == second))
        self._complete_from_worker(first_future, result=first)

        self.assertEqual(self.controller.current_target, second)
        self.assertEqual([call[0] for call in self.service.root_calls], [second])
        self.assertEqual(self.recorder.targets, [second])

    def test_same_parent_and_generation_accept_only_latest_request_id(self):
        target = self._target()
        root = self._capture_and_wait(target)
        older_id = self.controller.load_children(target, root, generation=4)
        older = self.service.children_calls[-1]["request"]
        newer_id = self.controller.load_children(target, root, generation=4)
        newer = self.service.children_calls[-1]["request"]
        self.assertNotEqual(older_id, newer_id)

        latest_page = self._page(target, root, 4, "latest")
        old_page = self._page(target, root, 4, "older")
        self._complete_from_worker(newer.future, result=latest_page)
        self.assertTrue(self._pump_until(lambda: len(self.recorder.pages) == 1))
        self._complete_from_worker(older.future, result=old_page)

        self.assertEqual(self.recorder.pages, [latest_page])
        self.assertEqual(self.controller.last_good_page(target, root), latest_page)
        self.assertTrue(all(self.recorder.gui_thread_flags))

    def test_terminal_latest_id_is_pruned_without_accepting_older_pending_result(self):
        target = self._target()
        root = self._capture_and_wait(target)
        self.controller.load_children(target, root, generation=6)
        older = self.service.children_calls[-1]["request"]
        self.controller.load_children(target, root, generation=6)
        newer = self.service.children_calls[-1]["request"]
        latest_key = (target.scope_key, self.controller._target_epoch, "load_children", root.node_ref)
        newest_page = self._page(target, root, 6, "newest")

        self._complete_from_worker(newer.future, result=newest_page)
        self.assertTrue(self._pump_until(lambda: self.recorder.pages == [newest_page]))
        self.assertNotIn(latest_key, self.controller._latest_request_by_parent)

        self._complete_from_worker(older.future, result=self._page(target, root, 6, "older"))
        self.assertEqual(self.recorder.pages, [newest_page])
        self.assertEqual(self.controller.last_good_page(target, root), newest_page)

    def test_last_good_lru_respects_entry_and_utf8_byte_budgets(self):
        target = self._target()
        self._capture_and_wait(target)

        entry_count = 66
        parents = [TreeNode(f"parent-{index}", "schema", f"schema-{index}") for index in range(entry_count)]
        first_page = None
        latest_page = None
        for index, parent in enumerate(parents):
            self.controller.load_children(target, parent, generation=index)
            request = self.service.children_calls[-1]["request"]
            page = self._page(target, parent, index, f"entry-{index}")
            if index == 0:
                first_page = page
            latest_page = page
            self._complete_from_worker(request.future, result=page)
            self.assertTrue(self._pump_until(lambda: self.recorder.pages and self.recorder.pages[-1] is page))
            if index == entry_count - 3:
                self.assertIs(self.controller.last_good_page(target, parents[0]), first_page)

        self.assertEqual(len(self.controller._last_good_pages), 64)
        self.assertIs(self.controller.last_good_page(target, parents[0]), first_page)
        self.assertIsNone(self.controller.last_good_page(target, parents[1]))
        self.assertIsNotNone(self.controller.last_good_page(target, parents[-1]))
        self.assertIsNotNone(latest_page)
        self.assertIsNotNone(first_page)

        large_parent_a = TreeNode("large-parent-a", "schema", "large-a")
        large_parent_b = TreeNode("large-parent-b", "schema", "large-b")
        large_page_a = TreePage(
            target=target,
            parent_ref=large_parent_a.node_ref,
            nodes=(TreeNode("large-node-a", "schema", "中" * 400_000, parent_ref=large_parent_a.node_ref),),
            next_cursor=None,
            generation=100,
            refresh_epoch=0,
            loaded_at=1.0,
        )
        self.controller.load_children(target, large_parent_a, generation=100)
        request_a = self.service.children_calls[-1]["request"]
        self._complete_from_worker(request_a.future, result=large_page_a)
        self.assertTrue(self._pump_until(lambda: self.recorder.pages[-1] is large_page_a))
        self.assertIs(self.controller.last_good_page(target, large_parent_a), large_page_a)

        large_page_b = TreePage(
            target=target,
            parent_ref=large_parent_b.node_ref,
            nodes=(TreeNode("large-node-b", "schema", "界" * 400_000, parent_ref=large_parent_b.node_ref),),
            next_cursor=None,
            generation=101,
            refresh_epoch=0,
            loaded_at=1.0,
        )
        self.controller.load_children(target, large_parent_b, generation=101)
        request_b = self.service.children_calls[-1]["request"]
        self._complete_from_worker(request_b.future, result=large_page_b)
        self.assertTrue(self._pump_until(lambda: self.recorder.pages[-1] is large_page_b))
        self.assertLessEqual(self.controller._last_good_cache_bytes, 2 * 1024 * 1024)
        self.assertIsNone(self.controller.last_good_page(target, large_parent_a))
        self.assertIs(self.controller.last_good_page(target, large_parent_b), large_page_b)

        oversized_parent = TreeNode("oversized-parent", "schema", "oversized")
        oversized_page = TreePage(
            target=target,
            parent_ref=oversized_parent.node_ref,
            nodes=(
                TreeNode(
                    "oversized-node-ref",
                    "schema",
                    "x" * (2 * 1024 * 1024 + 1),
                    parent_ref=oversized_parent.node_ref,
                ),
            ),
            next_cursor=None,
            generation=102,
            refresh_epoch=0,
            loaded_at=1.0,
        )
        self.controller.load_children(target, oversized_parent, generation=102)
        oversized_request = self.service.children_calls[-1]["request"]
        self._complete_from_worker(oversized_request.future, result=oversized_page)
        self.assertTrue(self._pump_until(lambda: self.recorder.pages[-1] is oversized_page))
        self.assertIsNone(self.controller.last_good_page(target, oversized_parent))
        self.assertLessEqual(self.controller._last_good_cache_bytes, 2 * 1024 * 1024)

        cursor_parent = TreeNode("large-cursor-parent", "schema", "cursor-parent")
        request_cursor = "游" * 700_000
        cursor_page = self._page(target, cursor_parent, 103, "small-page")
        self.controller.load_children(
            target,
            cursor_parent,
            cursor=request_cursor,
            generation=103,
        )
        cursor_request = self.service.children_calls[-1]["request"]
        self._complete_from_worker(cursor_request.future, result=cursor_page)
        self.assertTrue(self._pump_until(lambda: self.recorder.pages[-1] is cursor_page))
        self.assertIsNone(self.controller.last_good_page(target, cursor_parent, cursor=request_cursor))
        self.assertLessEqual(self.controller._last_good_cache_bytes, 2 * 1024 * 1024)

    def test_cancelled_running_page_result_is_discarded_but_kept_pending_until_terminal(self):
        target = self._target()
        root = self._capture_and_wait(target)
        request_id = self.controller.load_children(target, root, generation=12)
        request = self.service.children_calls[-1]["request"]

        self.assertFalse(self.controller.cancel(request_id))
        self.assertTrue(request._token.cancelled)
        self.assertIn(request_id, self.controller._requests)
        page = self._page(target, root, 12, "cancelled-late-page")
        self._complete_from_worker(request.future, result=page)

        self.assertFalse(self.recorder.pages)
        self.assertIsNone(self.controller.last_good_page(target, root))
        aggregate = self.controller.close()
        self.assertFalse(aggregate.done())
        self._complete_from_worker(self.cleanup_future, result=CleanupResult(True, 0))
        self.assertTrue(self._pump_until(aggregate.done))
        self.assertTrue(aggregate.result().completed)

    def test_cancelled_running_details_result_is_discarded(self):
        target = self._target()
        root = self._capture_and_wait(target)
        node = TreeNode("signed-table-ref", "table", "orders", parent_ref=root.node_ref)
        request_id = self.controller.inspect_node(target, node, generation=13)
        request = self.service.inspect_calls[-1][3]

        self.assertFalse(self.controller.cancel(request_id))
        self.assertTrue(request._token.cancelled)
        details = TreeNodeDetails(target, node.node_ref, "table", 0, 13)
        self._complete_from_worker(request.future, result=details)

        self.assertFalse(self.recorder.details)

    def test_pagination_preserves_cursor_and_local_error_keeps_last_good_page(self):
        target = self._target()
        root = self._capture_and_wait(target)
        first_id = self.controller.load_children(target, root, generation=8)
        first_request = self.service.children_calls[-1]["request"]
        first_page = self._page(target, root, 8, "first", cursor="opaque-cursor-2")
        self._complete_from_worker(first_request.future, result=first_page)
        self.assertTrue(self._pump_until(lambda: bool(self.recorder.pages)))

        next_id = self.controller.load_children(
            target,
            root,
            cursor=first_page.next_cursor,
            generation=8,
        )
        next_request = self.service.children_calls[-1]["request"]
        self.assertNotEqual(first_id, next_id)
        self.assertEqual(self.service.children_calls[-1]["cursor"], "opaque-cursor-2")
        self._complete_from_worker(
            next_request.future,
            error=ObjectTreeError("QUERY_FAILED", "raw profile or provider diagnostics must not escape"),
        )

        self.assertEqual(self.recorder.pages, [first_page])
        self.assertEqual(self.controller.last_good_page(target, root), first_page)
        self.assertEqual(len(self.recorder.failures), 1)
        failure = self.recorder.failures[0]
        self.assertIsInstance(failure, TreeControllerFailure)
        self.assertEqual(failure.code, "QUERY_FAILED")
        self.assertFalse(hasattr(failure, "message"))
        self.assertNotIn("raw profile", repr(failure))

        child = first_page.nodes[0]
        details_id = self.controller.inspect_node(target, child, generation=9)
        _details_target, details_node, details_generation, details_request = self.service.inspect_calls[-1]
        self.assertEqual(
            (details_id, _details_target, details_node, details_generation),
            (details_request.request_id, target, child, 9),
        )
        details = TreeNodeDetails(target, child.node_ref, "table", 0, 9)
        self._complete_from_worker(details_request.future, result=details)
        self.assertTrue(self._pump_until(lambda: bool(self.recorder.details)))
        self.assertEqual(self.recorder.details, [details])

    def test_mismatched_target_parent_or_generation_never_replaces_page(self):
        target = self._target()
        root = self._capture_and_wait(target)
        wrong_target = self._target("other-connection")
        other_parent = TreeNode("other-parent-ref", "schema", "Other")
        invalid_responses = (
            (3, self._page(wrong_target, root, 3, "wrong-target")),
            (4, self._page(target, other_parent, 4, "wrong-parent")),
            (5, self._page(target, root, 6, "wrong-generation")),
        )
        for generation, invalid_page in invalid_responses:
            self.controller.load_children(target, root, generation=generation)
            request = self.service.children_calls[-1]["request"]
            self._complete_from_worker(request.future, result=invalid_page)
            self.assertTrue(self._pump_until(lambda: request.future.done()))

        self.assertFalse(self.recorder.pages)
        self.assertEqual(self.controller.last_good_page(target, root), None)
        self.assertEqual([failure.code for failure in self.recorder.failures], ["STALE_RESPONSE"] * 3)

    def test_close_waits_for_capture_request_and_injected_cleanup_terminal_states(self):
        target = self._target()
        root = self._capture_and_wait(target)
        request_id = self.controller.load_children(target, root, generation=5)
        request = self.service.children_calls[-1]["request"]
        late_capture_id = self.controller.capture_target("conn-late", database_scope="late-db")
        late_capture = self.capture_futures[-1]
        self.assertFalse(self.controller.cancel(request_id))

        aggregate = self.controller.close()
        self.assertIs(self.controller.close(), aggregate)
        self.assertTrue(self.controller.is_closing)
        self.assertTrue(request._token.cancelled)
        self.assertTrue(late_capture.cancel_requested)
        self.assertEqual(self.cleanup_calls, ["called"])
        self.assertEqual(self.service.close_calls, 0)
        self.assertFalse(aggregate.done())

        self._complete_from_worker(self.cleanup_future, result=CleanupResult(True, 1))
        self.assertFalse(aggregate.done())
        late_page = self._page(target, root, 5, "late")
        self._complete_from_worker(request.future, result=late_page)
        self.assertFalse(aggregate.done())
        self.assertFalse(self.recorder.pages)

        self._complete_from_worker(late_capture, result=self._target("conn-late", database="late-db"))
        self.assertTrue(self._pump_until(aggregate.done))
        self.assertEqual(aggregate.result(), TreeControllerCleanupResult(True, 0, ""))
        self.assertEqual(len(self.recorder.cleanups), 1)
        self.assertEqual(self.controller.current_target, target)
        self.assertFalse(self.recorder.pages)
        self.assertEqual(self.cleanup_calls, ["called"])
        self.assertEqual(self.service.close_calls, 0)

    def test_failed_cleanup_is_not_reported_as_complete(self):
        aggregate = self.controller.close()
        self._complete_from_worker(self.cleanup_future, result=CleanupResult(False, 0))

        self.assertTrue(self._pump_until(aggregate.done))
        self.assertFalse(aggregate.result().completed)
        self.assertFalse(self.controller.is_cleanup_complete)
        self.assertEqual(self.recorder.cleanups, [TreeControllerCleanupResult(False, 0, "")])

    def test_destroyed_qobject_drops_a_late_future_signal(self):
        from PyQt6 import sip

        target = self._target()
        root = self._capture_and_wait(target)
        self.controller.load_children(target, root, generation=10)
        request = self.service.children_calls[-1]["request"]
        controller = self.controller
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(controller, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(controller))

        late_page = self._page(target, root, 10, "late-after-delete")
        self._complete_from_worker(request.future, result=late_page)
        self.app.processEvents()
        self.assertFalse(self.recorder.pages)


if __name__ == "__main__":
    unittest.main()
