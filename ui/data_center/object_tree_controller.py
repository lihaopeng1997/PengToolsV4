"""Qt-safe adapter for the injected Data Center object-tree service.

The controller accepts an already composed tree service and a target-capture
callable. It does not read connection profiles, build provider hosts, or run
database work itself. Futures report through a queued Qt signal so that a
widget owner can reject stale responses on its own thread.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass, replace
from typing import Any, Callable
import weakref

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot

from tools.data_center.object_tree import (
    CleanupResult,
    ObjectTreeError,
    ObjectTreeRequest,
    ObjectTreeService,
    TreeNode,
    TreeNodeDetails,
    TreePage,
    TreeTarget,
)

_MAX_LAST_GOOD_PAGE_ENTRIES = 64
_MAX_LAST_GOOD_CACHE_BYTES = 2 * 1024 * 1024


_SAFE_TREE_ERROR_CODES = frozenset(
    {
        "ARGUMENT_INVALID",
        "CANCELLED",
        "CAPABILITY_UNAVAILABLE",
        "CONNECT_FAILED",
        "PROFILE_MISMATCH",
        "PROFILE_UNAVAILABLE",
        "QUERY_FAILED",
        "SCOPE_DENIED",
        "STALE_CURSOR",
        "STALE_NODE",
        "TIMEOUT",
        "UNSUPPORTED_PROVIDER",
    }
)
_SAFE_CONTROLLER_ERROR_CODES = _SAFE_TREE_ERROR_CODES | frozenset(
    {
        "CAPTURE_CONTRACT_INVALID",
        "CAPTURE_FAILED",
        "CLEANUP_CONTRACT_INVALID",
        "CLEANUP_FAILED",
        "CONTROLLER_CLOSED",
        "FUTURE_CONTRACT_INVALID",
        "REQUEST_CONTRACT_INVALID",
        "REQUEST_ID_INVALID",
        "STALE_RESPONSE",
        "STALE_TARGET",
    }
)


@dataclass(frozen=True, slots=True)
class TreeControllerFailure:
    """Safe failure summary; it intentionally carries no exception/profile."""

    operation: str
    request_id: str
    parent_ref: str
    generation: int
    code: str


@dataclass(frozen=True, slots=True)
class TreeControllerCleanupResult:
    """Aggregate status after all tracked work and injected cleanup finish."""

    completed: bool
    pending_operations: int = 0
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class _RequestState:
    request: ObjectTreeRequest
    target: TreeTarget
    target_epoch: int
    operation: str
    parent_ref: str
    generation: int
    cursor: str | None
    discard_result: bool = False


@dataclass(frozen=True, slots=True)
class _FutureCompletion:
    operation: str
    correlation_id: str
    target: TreeTarget | None = None
    target_epoch: int = 0
    parent_ref: str = ""
    generation: int = 0
    cursor: str | None = None
    value: Any = None
    error_code: str = ""


class _FutureSignalBridge(QObject):
    completed = pyqtSignal(object)


class ObjectTreeController(QObject):
    """Own request correlation while leaving provider ownership to composition."""

    target_changed = pyqtSignal(object)
    root_ready = pyqtSignal(object, object, int)
    page_ready = pyqtSignal(object)
    details_ready = pyqtSignal(object)
    request_failed = pyqtSignal(object)
    cleanup_finished = pyqtSignal(object)

    def __init__(
        self,
        *,
        service: ObjectTreeService,
        target_capture: Callable[..., Future[TreeTarget]],
        cleanup: Callable[[], Future[Any]],
    ) -> None:
        super().__init__()
        if service is None or not callable(getattr(service, "root", None)):
            raise TypeError("service must provide the object-tree public API")
        if not callable(target_capture):
            raise TypeError("target_capture must be callable")
        if not callable(cleanup):
            raise TypeError("cleanup must be callable")

        self._service = service
        self._target_capture = target_capture
        self._cleanup = cleanup
        self._bridge = _FutureSignalBridge(self)
        self._bridge.completed.connect(self._on_future_completion, Qt.ConnectionType.QueuedConnection)

        self._target: TreeTarget | None = None
        self._root: TreeNode | None = None
        self._target_epoch = 0
        self._sequence = 0
        self._active_capture_id: str | None = None
        self._captures: dict[str, Future[Any]] = {}
        self._requests: dict[str, _RequestState] = {}
        self._latest_request_by_parent: dict[tuple[Any, ...], tuple[int, str]] = {}
        self._last_good_pages: OrderedDict[tuple[Any, ...], TreePage] = OrderedDict()
        self._last_good_page_sizes: dict[tuple[Any, ...], int] = {}
        self._last_good_cache_bytes = 0

        self._closing = False
        self._cleanup_future: Future[Any] | None = None
        self._cleanup_terminal = False
        self._cleanup_completed = False
        self._cleanup_error_code = ""
        self._close_result_future: Future[TreeControllerCleanupResult] | None = None
        self._cleanup_signal_emitted = False

    @property
    def current_target(self) -> TreeTarget | None:
        return self._target

    @property
    def root_node(self) -> TreeNode | None:
        return self._root

    @property
    def is_closing(self) -> bool:
        return self._closing

    @property
    def is_cleanup_complete(self) -> bool:
        return bool(self._cleanup_signal_emitted and self._cleanup_completed)

    def capture_target(
        self,
        connection_id: str,
        *,
        database_scope: str = "",
        redis_prefix: str | None = None,
    ) -> str:
        """Asynchronously capture one immutable scope, then issue its zero-I/O root."""

        self._assert_qt_thread()
        if self._closing:
            self._emit_failure("capture_target", "", "", 0, "CONTROLLER_CLOSED")
            return ""
        if not isinstance(connection_id, str) or not connection_id:
            self._emit_failure("capture_target", "", "", 0, "ARGUMENT_INVALID")
            return ""

        capture_id = self._next_id("capture")
        previous_id = self._active_capture_id
        self._active_capture_id = capture_id
        try:
            future = self._target_capture(
                connection_id,
                database_scope=database_scope,
                redis_prefix=redis_prefix,
            )
        except Exception:
            self._active_capture_id = None
            self._emit_failure("capture_target", capture_id, "", 0, "CAPTURE_FAILED")
            return capture_id
        if not isinstance(future, Future):
            self._active_capture_id = None
            self._emit_failure("capture_target", capture_id, "", 0, "CAPTURE_CONTRACT_INVALID")
            return capture_id

        self._captures[capture_id] = future
        if previous_id is not None:
            previous = self._captures.get(previous_id)
            if previous is not None:
                try:
                    previous.cancel()
                except Exception:
                    pass
        self._watch_future(
            future,
            _FutureCompletion("capture_target", capture_id, target_epoch=self._target_epoch),
        )
        return capture_id

    def load_children(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        cursor: str | None = None,
        refresh: bool = False,
        generation: int = 0,
    ) -> str:
        """Load one exact signed parent page; cursors are passed through unchanged."""

        self._assert_qt_thread()
        if not self._accept_target(target, "load_children", node):
            return ""
        if not isinstance(node, TreeNode):
            self._emit_failure("load_children", "", "", generation, "ARGUMENT_INVALID")
            return ""

        try:
            if refresh:
                self._service.invalidate(target, node)
            request = self._service.submit_children(
                target,
                node,
                cursor=cursor,
                refresh=refresh,
                generation=generation,
            )
        except Exception as exc:
            self._emit_failure("load_children", "", node.node_ref, generation, self._error_code(exc))
            return ""
        return self._track_request(request, target, "load_children", node.node_ref, cursor, generation)

    def inspect_node(self, target: TreeTarget, node: TreeNode, *, generation: int = 0) -> str:
        """Inspect a service-issued node without deriving identity from display text."""

        self._assert_qt_thread()
        if not self._accept_target(target, "inspect_node", node):
            return ""
        if not isinstance(node, TreeNode):
            self._emit_failure("inspect_node", "", "", generation, "ARGUMENT_INVALID")
            return ""
        try:
            request = self._service.inspect_node(target, node, generation=generation)
        except Exception as exc:
            self._emit_failure("inspect_node", "", node.node_ref, generation, self._error_code(exc))
            return ""
        return self._track_request(request, target, "inspect_node", node.node_ref, None, generation)

    def cancel(self, request_id: str) -> bool:
        """Request cooperative cancellation while retaining references to terminal."""

        self._assert_qt_thread()
        capture = self._captures.get(str(request_id))
        if capture is not None:
            if self._active_capture_id == request_id:
                self._active_capture_id = None
            try:
                return bool(capture.cancel())
            except Exception:
                return False

        tracked = self._requests.get(str(request_id))
        if tracked is None:
            return False
        self._requests[str(request_id)] = replace(tracked, discard_result=True)
        try:
            return bool(tracked.request.cancel())
        except Exception:
            return False

    def last_good_page(
        self,
        target: TreeTarget,
        parent: TreeNode,
        *,
        cursor: str | None = None,
    ) -> TreePage | None:
        """Return the last accepted page for this exact target and signed parent."""

        if target != self._target or not isinstance(parent, TreeNode):
            return None
        key = (target.scope_key, self._target_epoch, parent.node_ref, cursor)
        page = self._last_good_pages.get(key)
        if page is not None:
            self._last_good_pages.move_to_end(key)
        return page

    def close(self) -> Future[TreeControllerCleanupResult]:
        """Cancel pending work and await the injected composition cleanup asynchronously."""

        self._assert_qt_thread()
        if self._close_result_future is not None:
            return self._close_result_future

        self._closing = True
        self._close_result_future = Future()
        self._active_capture_id = None
        for future in tuple(self._captures.values()):
            try:
                future.cancel()
            except Exception:
                pass
        for state in tuple(self._requests.values()):
            try:
                state.request.cancel()
            except Exception:
                pass

        try:
            cleanup_future = self._cleanup()
        except Exception:
            self._cleanup_terminal = True
            self._cleanup_error_code = "CLEANUP_FAILED"
            self._maybe_finish_cleanup()
            return self._close_result_future
        if not isinstance(cleanup_future, Future):
            self._cleanup_terminal = True
            self._cleanup_error_code = "CLEANUP_CONTRACT_INVALID"
            self._maybe_finish_cleanup()
            return self._close_result_future

        self._cleanup_future = cleanup_future
        self._watch_future(cleanup_future, _FutureCompletion("cleanup", "cleanup"))
        return self._close_result_future

    def _track_request(
        self,
        request: ObjectTreeRequest,
        target: TreeTarget,
        operation: str,
        parent_ref: str,
        cursor: str | None,
        generation: int,
    ) -> str:
        if not isinstance(request, ObjectTreeRequest) or not isinstance(request.future, Future):
            self._emit_failure(operation, "", parent_ref, generation, "REQUEST_CONTRACT_INVALID")
            return ""
        request_id = str(request.request_id or "")
        if not request_id or request_id in self._requests:
            try:
                request.cancel()
            except Exception:
                pass
            self._emit_failure(operation, request_id, parent_ref, generation, "REQUEST_ID_INVALID")
            return ""

        state = _RequestState(
            request=request,
            target=target,
            target_epoch=self._target_epoch,
            operation=operation,
            parent_ref=parent_ref,
            generation=generation,
            cursor=cursor,
        )
        self._requests[request_id] = state
        latest_key = (target.scope_key, state.target_epoch, operation, parent_ref)
        self._latest_request_by_parent[latest_key] = (generation, request_id)
        self._watch_future(
            request.future,
            _FutureCompletion(
                operation,
                request_id,
                target,
                state.target_epoch,
                parent_ref,
                generation,
                cursor,
            ),
        )
        return request_id

    def _watch_future(self, future: Future[Any], context: _FutureCompletion) -> None:
        dispatcher_ref = weakref.ref(self._bridge)

        def completed(done: Future[Any]) -> None:
            try:
                value = done.result()
                error_code = ""
            except BaseException as exc:
                value = None
                error_code = ObjectTreeController._error_code(exc)

            if context.operation == "cleanup":
                if error_code:
                    error_code = "CLEANUP_FAILED"
                elif isinstance(value, CleanupResult):
                    value = bool(value.completed)
                elif isinstance(value, bool):
                    value = value
                else:
                    try:
                        completed_value = getattr(value, "completed", None)
                    except Exception:
                        completed_value = None
                    if isinstance(completed_value, bool):
                        value = completed_value
                    else:
                        value = False
                        error_code = "CLEANUP_CONTRACT_INVALID"

            message = replace(context, value=value, error_code=error_code)
            dispatcher = dispatcher_ref()
            if dispatcher is None:
                return
            try:
                dispatcher.completed.emit(message)
            except RuntimeError:
                # The QObject may have been deleted while native work finished.
                return

        try:
            future.add_done_callback(completed)
        except Exception:
            message = replace(
                context,
                value=None,
                error_code="FUTURE_CONTRACT_INVALID",
            )
            try:
                self._bridge.completed.emit(message)
            except RuntimeError:
                pass

    @pyqtSlot(object)
    def _on_future_completion(self, completion: _FutureCompletion) -> None:
        self._assert_qt_thread()
        if not isinstance(completion, _FutureCompletion):
            return
        if completion.operation == "capture_target":
            self._finish_capture(completion)
        elif completion.operation == "cleanup":
            self._finish_cleanup_future(completion)
        else:
            self._finish_request(completion)

    def _finish_capture(self, completion: _FutureCompletion) -> None:
        self._captures.pop(completion.correlation_id, None)
        if self._closing or completion.correlation_id != self._active_capture_id:
            self._maybe_finish_cleanup()
            return
        self._active_capture_id = None
        if completion.error_code:
            self._emit_failure("capture_target", completion.correlation_id, "", 0, completion.error_code)
            return
        target = completion.value
        if not isinstance(target, TreeTarget):
            self._emit_failure("capture_target", completion.correlation_id, "", 0, "CAPTURE_CONTRACT_INVALID")
            return
        try:
            root = self._service.root(target)
        except Exception as exc:
            self._emit_failure("capture_target", completion.correlation_id, "", 0, self._error_code(exc))
            return
        if not isinstance(root, TreeNode) or root.kind != "root":
            self._emit_failure("capture_target", completion.correlation_id, "", 0, "CAPTURE_CONTRACT_INVALID")
            return

        previous_epoch = self._target_epoch
        self._target_epoch += 1
        self._target = target
        self._root = root
        self._latest_request_by_parent.clear()
        self._last_good_pages.clear()
        self._last_good_page_sizes.clear()
        self._last_good_cache_bytes = 0
        for state in tuple(self._requests.values()):
            if state.target_epoch == previous_epoch:
                try:
                    state.request.cancel()
                except Exception:
                    pass
        self.target_changed.emit(target)
        self.root_ready.emit(target, root, 0)

    def _finish_request(self, completion: _FutureCompletion) -> None:
        state = self._requests.pop(completion.correlation_id, None)
        if state is None:
            self._maybe_finish_cleanup()
            return

        latest_key = (state.target.scope_key, state.target_epoch, state.operation, state.parent_ref)
        was_latest = self._latest_request_by_parent.get(latest_key) == (
            state.generation,
            completion.correlation_id,
        )
        if was_latest:
            self._latest_request_by_parent.pop(latest_key, None)

        if self._closing or state.target_epoch != self._target_epoch or state.target != self._target:
            self._maybe_finish_cleanup()
            return

        if state.discard_result or not was_latest:
            self._maybe_finish_cleanup()
            return
        if completion.error_code:
            self._emit_failure(
                state.operation,
                completion.correlation_id,
                state.parent_ref,
                state.generation,
                completion.error_code,
            )
            self._maybe_finish_cleanup()
            return

        value = completion.value
        if state.operation == "load_children":
            if (
                not isinstance(value, TreePage)
                or value.target != state.target
                or value.parent_ref != state.parent_ref
                or value.generation != state.generation
                or value.cache_state == "stale"
            ):
                self._emit_failure(
                    state.operation,
                    completion.correlation_id,
                    state.parent_ref,
                    state.generation,
                    "STALE_RESPONSE",
                )
                self._maybe_finish_cleanup()
                return
            cache_key = (state.target.scope_key, state.target_epoch, state.parent_ref, state.cursor)
            self._remember_last_good_page(cache_key, value)
            self.page_ready.emit(value)
        elif state.operation == "inspect_node":
            if (
                not isinstance(value, TreeNodeDetails)
                or value.target != state.target
                or value.node_ref != state.parent_ref
                or value.generation != state.generation
            ):
                self._emit_failure(
                    state.operation,
                    completion.correlation_id,
                    state.parent_ref,
                    state.generation,
                    "STALE_RESPONSE",
                )
                self._maybe_finish_cleanup()
                return
            self.details_ready.emit(value)
        self._maybe_finish_cleanup()

    def _remember_last_good_page(self, key: tuple[Any, ...], page: TreePage) -> None:
        """Keep a modest LRU estimate of retained UTF-8 DTO text, never the work itself."""

        request_cursor = key[-1] if key and isinstance(key[-1], str) else None
        size_bytes = self._estimated_page_size_bytes(page, request_cursor=request_cursor)
        self._remove_last_good_page(key)
        if size_bytes > _MAX_LAST_GOOD_CACHE_BYTES:
            return

        self._last_good_pages[key] = page
        self._last_good_page_sizes[key] = size_bytes
        self._last_good_cache_bytes += size_bytes
        while (
            len(self._last_good_pages) > _MAX_LAST_GOOD_PAGE_ENTRIES
            or self._last_good_cache_bytes > _MAX_LAST_GOOD_CACHE_BYTES
        ):
            oldest_key = next(iter(self._last_good_pages))
            self._remove_last_good_page(oldest_key)

    def _remove_last_good_page(self, key: tuple[Any, ...]) -> None:
        if key not in self._last_good_pages:
            return
        self._last_good_pages.pop(key, None)
        self._last_good_cache_bytes -= self._last_good_page_sizes.pop(key, 0)

    @staticmethod
    def _estimated_page_size_bytes(page: TreePage, *, request_cursor: str | None = None) -> int:
        """Estimate serialized DTO size plus its input cursor and cache-key overhead."""

        text_values = [
            page.target.connection_id,
            page.target.profile_revision,
            page.target.provider,
            page.target.mode,
            page.target.database,
            page.target.redis_prefix,
            page.parent_ref,
            page.next_cursor,
            page.cache_state,
            page.capability_notice,
            *page.notices,
        ]
        for node in page.nodes:
            text_values.extend(
                (
                    node.node_ref,
                    node.kind,
                    node.name,
                    node.database,
                    node.schema,
                    node.parent_ref,
                    node.notice,
                )
            )

        utf8_bytes = sum(
            len(value.encode("utf-8", "replace"))
            for value in text_values
            if isinstance(value, str)
        )
        cursor_bytes = len(request_cursor.encode("utf-8", "replace")) if request_cursor else 0
        # Include approximate JSON punctuation, tuple/index keys, and per-record metadata.
        return utf8_bytes + cursor_bytes + 384 + len(page.nodes) * 96 + len(page.notices) * 16

    def _finish_cleanup_future(self, completion: _FutureCompletion) -> None:
        self._cleanup_future = None
        self._cleanup_terminal = True
        self._cleanup_completed = not completion.error_code and completion.value is True
        self._cleanup_error_code = completion.error_code
        self._maybe_finish_cleanup()

    def _maybe_finish_cleanup(self) -> None:
        if (
            not self._closing
            or self._cleanup_signal_emitted
            or not self._cleanup_terminal
            or self._captures
            or self._requests
        ):
            return
        self._cleanup_signal_emitted = True
        result = TreeControllerCleanupResult(
            completed=self._cleanup_completed,
            pending_operations=0,
            error_code=self._cleanup_error_code,
        )
        aggregate = self._close_result_future
        if aggregate is not None and not aggregate.done():
            aggregate.set_result(result)
        self.cleanup_finished.emit(result)

    def _accept_target(self, target: TreeTarget, operation: str, node: Any) -> bool:
        if self._closing:
            self._emit_failure(operation, "", getattr(node, "node_ref", ""), 0, "CONTROLLER_CLOSED")
            return False
        if not isinstance(target, TreeTarget) or target != self._target:
            self._emit_failure(operation, "", getattr(node, "node_ref", ""), 0, "STALE_TARGET")
            return False
        return True

    def _emit_failure(
        self,
        operation: str,
        request_id: str,
        parent_ref: str,
        generation: int,
        code: str,
    ) -> None:
        safe_code = code if code in _SAFE_CONTROLLER_ERROR_CODES else "QUERY_FAILED"
        self.request_failed.emit(
            TreeControllerFailure(
                operation=str(operation),
                request_id=str(request_id or ""),
                parent_ref=str(parent_ref or ""),
                generation=int(generation) if isinstance(generation, int) else 0,
                code=str(safe_code),
            )
        )

    @staticmethod
    def _error_code(exc: BaseException) -> str:
        if isinstance(exc, CancelledError):
            return "CANCELLED"
        if isinstance(exc, ObjectTreeError) and exc.code in _SAFE_TREE_ERROR_CODES:
            return exc.code
        return "QUERY_FAILED"

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence}"

    def _assert_qt_thread(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("ObjectTreeController methods must run on its Qt thread")


__all__ = [
    "ObjectTreeController",
    "TreeControllerCleanupResult",
    "TreeControllerFailure",
]
