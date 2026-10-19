"""Per-connection owner for lazy object-directory metadata.

The owner wires the public application composition to the Qt-safe controller.
It never reads catalog/profile fields and never constructs a provider itself.
Each connection gets an independent owner so closing one directory cannot
cancel another connection's metadata work.
"""

from __future__ import annotations

from concurrent.futures import Future
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from tools.data_center.object_tree import TreeNode, TreeNodeDetails, TreePage, TreeTarget
from tools.data_center.object_tree_composition import ObjectTreeComposition
from ui.data_center.object_tree_controller import (
    ObjectTreeController,
    TreeControllerCleanupResult,
    TreeControllerFailure,
)


class ObjectTreeWorkspace(QObject):
    """Own one fixed connection's capture, service, and cleanup lifecycle."""

    root_ready = pyqtSignal(str, object, object, int)
    page_ready = pyqtSignal(str, object, object, int)
    details_ready = pyqtSignal(str, object, object, int)
    request_failed = pyqtSignal(str, object)
    cleanup_finished = pyqtSignal(str, object)

    def __init__(
        self,
        connection_id: str,
        *,
        composition_factory: Callable[[str], ObjectTreeComposition] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        selected_id = str(connection_id or "")
        if not selected_id:
            raise ValueError("connection_id is required")
        self.connection_id = selected_id
        factory = composition_factory or (lambda _connection_id: ObjectTreeComposition())
        composition = factory(selected_id)
        if composition is None:
            raise TypeError("composition_factory must return an object-tree composition")
        service = getattr(composition, "service", None)
        capture = getattr(composition, "submit_target_capture", None)
        cleanup = getattr(composition, "close", None)
        if service is None or not callable(capture) or not callable(cleanup):
            raise TypeError("composition must expose service, submit_target_capture, and close")

        self._composition = composition
        self._controller = ObjectTreeController(
            service=service,
            target_capture=capture,
            cleanup=cleanup,
        )
        self._controller.root_ready.connect(self._on_root_ready)
        self._controller.page_ready.connect(self._on_page_ready)
        self._controller.details_ready.connect(self._on_details_ready)
        self._controller.request_failed.connect(self._on_request_failed)
        self._controller.cleanup_finished.connect(self._on_cleanup_finished)

        self._target: TreeTarget | None = None
        self._root: TreeNode | None = None
        self._generation = 0
        self._capture_scope = ""
        self._redis_prefix: str | None = None
        self._capture_pending = False
        self._latest_cursor_by_parent: dict[tuple[str, int], str | None] = {}
        self._close_future: Future[TreeControllerCleanupResult] | None = None

    @property
    def target(self) -> TreeTarget | None:
        return self._target

    @property
    def root_node(self) -> TreeNode | None:
        return self._root

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def table_source_service(self):
        """Expose the signed table-source service without leaking composition internals."""
        return self._composition.service

    @property
    def is_closing(self) -> bool:
        return self._close_future is not None

    @property
    def capture_pending(self) -> bool:
        return self._capture_pending

    def capture_target(
        self,
        *,
        database_scope: str = "",
        redis_prefix: str | None = None,
    ) -> str:
        """Capture an exact profile snapshot after an explicit tree action."""
        if self.is_closing:
            return ""
        self._generation += 1
        self._capture_scope = database_scope
        self._redis_prefix = redis_prefix
        self._target = None
        self._root = None
        self._latest_cursor_by_parent.clear()
        self._capture_pending = True
        return self._controller.capture_target(
            self.connection_id,
            database_scope=database_scope,
            redis_prefix=redis_prefix,
        )

    def refresh_target(self) -> str:
        """Recapture profile revision and root using the explicit prior scope."""
        return self.capture_target(
            database_scope=self._capture_scope,
            redis_prefix=self._redis_prefix,
        )

    def load_children(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        cursor: str | None = None,
        refresh: bool = False,
        generation: int | None = None,
    ) -> str:
        if self.is_closing:
            return ""
        selected_generation = self._generation if generation is None else int(generation)
        if target != self._target:
            return ""
        key = (node.node_ref, selected_generation)
        self._latest_cursor_by_parent[key] = cursor
        return self._controller.load_children(
            target,
            node,
            cursor=cursor,
            refresh=refresh,
            generation=selected_generation,
        )

    def inspect_node(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        generation: int | None = None,
    ) -> str:
        if self.is_closing or target != self._target:
            return ""
        selected_generation = self._generation if generation is None else int(generation)
        return self._controller.inspect_node(target, node, generation=selected_generation)

    def close(self) -> Future[TreeControllerCleanupResult]:
        """Cancel this connection's requests and retain owner until true cleanup."""
        if self._close_future is None:
            self._capture_pending = False
            self._latest_cursor_by_parent.clear()
            self._close_future = self._controller.close()
        return self._close_future

    def _on_root_ready(self, target: TreeTarget, root: TreeNode, _controller_generation: int) -> None:
        if self.is_closing:
            return
        self._capture_pending = False
        self._target = target
        self._root = root
        generation = self._generation
        self.root_ready.emit(self.connection_id, target, root, generation)

    def _on_page_ready(self, page: TreePage) -> None:
        cursor = self._latest_cursor_by_parent.pop(
            (page.parent_ref, int(page.generation)), None
        )
        if (
            self.is_closing
            or self._capture_pending
            or page.target != self._target
            or int(page.generation) != self._generation
        ):
            return
        self.page_ready.emit(self.connection_id, page, cursor, self._generation)

    def _on_details_ready(self, details: TreeNodeDetails) -> None:
        if (
            self.is_closing
            or self._capture_pending
            or details.target != self._target
            or int(details.generation) != self._generation
        ):
            return
        self.details_ready.emit(
            self.connection_id,
            details,
            self._target,
            self._generation,
        )

    def _on_request_failed(self, failure: TreeControllerFailure) -> None:
        if self.is_closing:
            return
        if str(getattr(failure, "operation", "")) == "capture_target":
            self._capture_pending = False
        else:
            self._latest_cursor_by_parent.pop(
                (str(getattr(failure, "parent_ref", "") or ""), int(getattr(failure, "generation", 0) or 0)),
                None,
            )
            if (
                self._capture_pending
                or self._target is None
                or int(getattr(failure, "generation", 0) or 0) != self._generation
            ):
                return
        self.request_failed.emit(self.connection_id, failure)

    def _on_cleanup_finished(self, result: TreeControllerCleanupResult) -> None:
        self.cleanup_finished.emit(self.connection_id, result)


__all__ = ["ObjectTreeWorkspace"]
