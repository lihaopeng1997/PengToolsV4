"""Bounded, host-owned lazy metadata tree for Data Center navigation.

The service keeps opaque node references and pagination state in memory.  It
never accepts SQL, a Redis key, a database name, or a collection name as a
scope by itself: each child request must carry a node reference issued for
the exact target and parent.  Agent metadata tools remain on their separate
search/describe and NoSQL policy paths.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from .host_metadata import MAX_METADATA_PAGE, MetadataHost, MetadataTarget
from .readonly_driver_adapters import canonical_provider


MAX_TREE_PAGE = 50
MAX_PENDING_REQUESTS = 64
MAX_METADATA_HOSTS = 16
MAX_NOSQL_TARGETS = 16
MAX_NODE_ENTRIES = 2048
MAX_NODE_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_PAGE_CACHE_ENTRIES = 64
MAX_PAGE_CACHE_BYTES = 2 * 1024 * 1024
MAX_CURSOR_ENTRIES = 256
MAX_CURSOR_REGISTRY_BYTES = 64 * 1024
MAX_TREE_NODE_IDENTITY_BYTES = 4096
MAX_TREE_NAME_BYTES = 1024
TREE_TTL_SECONDS = 15 * 60

_KNOWN_PROVIDERS = frozenset(
    {
        "oracle",
        "mysql",
        "oceanbase_oracle",
        "oceanbase_mysql",
        "dameng",
        "redis",
        "mongodb",
    }
)
_SAFE_ERROR_CODES = frozenset(
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


class ObjectTreeError(RuntimeError):
    """Stable error that does not include connector or profile diagnostics."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code if code in _SAFE_ERROR_CODES else "QUERY_FAILED"
        self.message = str(message)
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class TreeTarget:
    connection_id: str
    profile_revision: str
    provider: str
    mode: str = ""
    database: str = ""
    redis_prefix: str = ""

    def __post_init__(self) -> None:
        connection_id = _scope_text(self.connection_id, "connection_id", required=True)
        revision = _scope_text(self.profile_revision, "profile_revision", required=True)
        raw_provider = _scope_text(self.provider, "provider", required=True).casefold().replace("-", "_")
        mode = _scope_text(self.mode, "mode").casefold().replace("-", "_")
        aliases = {"mongo": "mongodb", "mongo_db": "mongodb", "dm": "dameng", "dm8": "dameng"}
        raw_provider = aliases.get(raw_provider, raw_provider)
        if raw_provider in {"ob", "oceanbase"}:
            if mode not in {"oracle", "mysql"}:
                raise ValueError("OceanBase tree target requires an exact mode")
            raw_provider = f"oceanbase_{mode}"
        elif raw_provider in {"ob_oracle", "ob_mysql"}:
            raw_provider = raw_provider.replace("ob_", "oceanbase_")
        elif raw_provider not in {"redis", "mongodb"}:
            try:
                raw_provider = canonical_provider(raw_provider, mode)
            except Exception:
                # Unknown providers remain displayable as a capability notice,
                # but the original string is bounded and can never reach SQL.
                if not raw_provider or len(raw_provider) > 48 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_." for ch in raw_provider):
                    raise ValueError("provider is invalid") from None
        if raw_provider in {"oracle", "mysql", "dameng"}:
            supported_modes = {"dameng", "dm", "dm8"} if raw_provider == "dameng" else {raw_provider}
            if mode and mode not in supported_modes:
                raise ValueError("provider and mode do not match")
            mode = raw_provider
        elif raw_provider in {"oceanbase_oracle", "oceanbase_mysql"}:
            expected_mode = raw_provider.removeprefix("oceanbase_")
            if mode and mode != expected_mode:
                raise ValueError("OceanBase provider and mode do not match")
            mode = expected_mode
        elif raw_provider in {"redis", "mongodb"}:
            if mode == "replicaset":
                mode = "replica_set"
            if not mode:
                mode = "standalone"
        # Database names and Redis prefixes are raw metadata scope values. Do
        # not normalize whitespace: it can be part of a quoted identifier/key.
        database = _scope_text(self.database, "database", trim=False)
        prefix = _scope_text(self.redis_prefix, "redis_prefix", trim=False)
        if raw_provider in {"mysql", "oceanbase_mysql", "mongodb"} and len(database.encode("utf-8", "strict")) > 512:
            raise ValueError("database scope is too long")
        if raw_provider == "redis" and len(prefix.encode("utf-8", "strict")) > 512:
            raise ValueError("Redis prefix is too long")
        if raw_provider != "redis" and prefix:
            raise ValueError("Redis prefix is only valid for Redis targets")
        if raw_provider == "redis" and mode not in {"standalone", "cluster"}:
            raise ValueError("Redis tree target requires a supported mode")
        if raw_provider == "mongodb" and mode not in {"", "standalone", "cluster", "replica_set", "srv"}:
            raise ValueError("Mongo tree target mode is unsupported")
        object.__setattr__(self, "connection_id", connection_id)
        object.__setattr__(self, "profile_revision", revision)
        object.__setattr__(self, "provider", raw_provider)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "database", database)
        object.__setattr__(self, "redis_prefix", prefix)

    @property
    def scope_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.connection_id,
            self.profile_revision,
            self.provider,
            self.mode,
            self.database,
            self.redis_prefix,
        )


@dataclass(frozen=True, slots=True)
class TreeNode:
    node_ref: str
    kind: str
    name: str
    database: str = ""
    schema: str = ""
    parent_ref: str = ""
    actionable: bool = True
    notice: str = ""

    def __repr__(self) -> str:
        return (
            f"TreeNode(node_ref={self.node_ref!r}, kind={self.kind!r}, name={self.name!r}, "
            f"database={self.database!r}, schema={self.schema!r}, actionable={self.actionable!r})"
        )


@dataclass(frozen=True, slots=True)
class VerifiedTableRef:
    """Raw table identity recovered from the live signed tree registry.

    The tree generation and refresh epoch belong to the directory namespace;
    they are deliberately kept separate from a manual tab's generation.
    """

    target: TreeTarget
    node_ref: str
    tree_generation: int
    refresh_epoch: int
    kind: str
    raw_name: str
    database: str
    schema: str


@dataclass(frozen=True, slots=True)
class TreePage:
    target: TreeTarget
    parent_ref: str
    nodes: tuple[TreeNode, ...]
    next_cursor: str | None
    generation: int
    refresh_epoch: int
    loaded_at: float
    cache_state: str = "fresh"
    capability_notice: str = ""
    notices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TreeNodeDetails:
    target: TreeTarget
    node_ref: str
    value_type: str
    ttl_seconds: int
    generation: int


@dataclass(frozen=True, slots=True)
class CleanupResult:
    completed: bool
    owners_closed: int


class _Cancellation:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> bool:
        was_set = self._event.is_set()
        self._event.set()
        return not was_set

    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(slots=True)
class ObjectTreeRequest:
    request_id: str
    future: Future[Any]
    _token: _Cancellation

    def cancel(self) -> bool:
        self._token.cancel()
        return self.future.cancel()


@dataclass(slots=True)
class _NodeRecord:
    target_key: tuple[str, str, str, str, str, str]
    kind: str
    name: str
    database: str
    schema: str
    parent_ref: str
    prefix: str
    identity: bytes | str | None
    actionable: bool
    notice: str
    generation: int
    refresh_epoch: int
    expires_at: float
    byte_cost: int


@dataclass(slots=True)
class _CursorRecord:
    target_key: tuple[str, str, str, str, str, str]
    parent_ref: str
    refresh_epoch: int
    backend_kind: str
    backend_cursor: Any
    expires_at: float
    byte_cost: int
    reserved: bool = False
    consumed: bool = False


@dataclass(slots=True)
class _PageCacheRecord:
    page: TreePage
    expires_at: float
    byte_cost: int


class ObjectTreeService:
    """One bounded worker for user directory metadata and signed node refs."""

    def __init__(
        self,
        *,
        relational_host: Any = None,
        nosql_host: Any = None,
        max_pending: int = MAX_PENDING_REQUESTS,
        page_size: int = MAX_TREE_PAGE,
        cache_ttl_seconds: float = TREE_TTL_SECONDS,
        node_ttl_seconds: float = TREE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or not 1 <= max_pending <= MAX_PENDING_REQUESTS:
            raise ValueError("max_pending is invalid")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_TREE_PAGE:
            raise ValueError("page_size is invalid")
        if cache_ttl_seconds <= 0 or node_ttl_seconds <= 0:
            raise ValueError("tree cache TTL must be positive")
        self._relational_host = relational_host
        self._nosql_host = nosql_host
        self._page_size = int(page_size)
        self._cache_ttl = float(cache_ttl_seconds)
        self._node_ttl = float(node_ttl_seconds)
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="data-center-object-tree")
        self._slots = threading.BoundedSemaphore(max_pending)
        self._lock = threading.RLock()
        self._closed = False
        self._cleanup_future: Future[CleanupResult] | None = None
        self._request_seq = 0
        self._active: dict[str, tuple[_Cancellation, Future[Any] | None]] = {}
        self._nodes: OrderedDict[str, _NodeRecord] = OrderedDict()
        self._node_bytes = 0
        self._roots: dict[tuple[str, str, str, str, str, str], str] = {}
        self._cursors: OrderedDict[str, _CursorRecord] = OrderedDict()
        self._cursor_bytes = 0
        self._pages: OrderedDict[tuple[Any, ...], _PageCacheRecord] = OrderedDict()
        self._page_bytes = 0
        self._parent_epochs: dict[tuple[Any, ...], int] = {}
        self._latest_requests: dict[tuple[Any, ...], tuple[int, int]] = {}
        self._metadata_hosts: OrderedDict[tuple[Any, ...], MetadataHost] = OrderedDict()
        self._nosql_factories: dict[tuple[Any, ...], Any] = {}
        self._nosql_leases: dict[tuple[Any, ...], Any] = {}

    def root(self, target: TreeTarget) -> TreeNode:
        """Issue the explicit connection-root node without profile or DB I/O."""

        if not isinstance(target, TreeTarget):
            raise TypeError("target must be a TreeTarget")
        with self._lock:
            if self._closed:
                raise ObjectTreeError("SESSION_NOT_FOUND", "object tree service is closed")
            self._prune_locked()
            existing_ref = self._roots.get(target.scope_key)
            if existing_ref:
                existing = self._nodes.get(existing_ref)
                if existing is not None:
                    existing.expires_at = self._clock() + self._node_ttl
                    return _public_node(existing_ref, existing)
            node = self._issue_node_locked(
                target,
                kind="root",
                name="连接",
                database=target.database,
                schema="",
                parent_ref="",
                prefix=target.redis_prefix,
                identity=None,
                actionable=True,
            )
            if node is None:
                raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "object tree node budget is full")
            self._roots[target.scope_key] = node.node_ref
            return node

    def submit_children(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        cursor: str | None = None,
        refresh: bool = False,
        generation: int = 0,
        page_size: int | None = None,
    ) -> ObjectTreeRequest:
        """Queue one bounded lazy page; caller can cancel without killing a driver."""

        return self._submit(target, node, cursor=cursor, refresh=refresh, generation=generation, page_size=page_size, details=False)

    def inspect_node(self, target: TreeTarget, node: TreeNode, *, generation: int = 0) -> ObjectTreeRequest:
        """Read Redis TYPE/TTL through a service-issued binary-key node ref."""

        return self._submit(target, node, cursor=None, refresh=False, generation=generation, page_size=1, details=True)

    def invalidate(self, target: TreeTarget, node: TreeNode | None = None) -> None:
        """Drop cached pages and issued descendants without performing I/O."""

        if not isinstance(target, TreeTarget):
            raise TypeError("target must be a TreeTarget")
        with self._lock:
            if node is not None:
                record = self._resolve_node_locked(target, node)
                refs = {node.node_ref}
                changed = True
                while changed:
                    changed = False
                    for ref, candidate in list(self._nodes.items()):
                        if candidate.target_key == target.scope_key and candidate.parent_ref in refs and ref not in refs:
                            refs.add(ref)
                            changed = True
                self._bump_epoch_locked(target, node.node_ref)
            else:
                refs = {ref for ref, record in self._nodes.items() if record.target_key == target.scope_key}
                self._parent_epochs = {
                    key: value for key, value in self._parent_epochs.items() if key[0] != target.scope_key
                }
            for key, page in list(self._pages.items()):
                if key[0] == target.scope_key and (node is None or key[1] in refs):
                    self._drop_page_locked(key)
            for token, cursor_record in list(self._cursors.items()):
                if cursor_record.target_key == target.scope_key and (node is None or cursor_record.parent_ref in refs):
                    self._drop_cursor_locked(token)
            if node is None:
                for ref in refs:
                    self._drop_node_locked(ref)
                self._roots.pop(target.scope_key, None)
            else:
                for ref in refs - {node.node_ref}:
                    self._drop_node_locked(ref)
                self._drop_node_locked(node.node_ref)
                self._roots.pop(target.scope_key, None) if node.kind == "root" else None

    def close(self) -> Future[CleanupResult]:
        """Request cooperative cleanup; completion waits for real worker exit."""

        with self._lock:
            if self._cleanup_future is not None:
                return self._cleanup_future
            self._closed = True
            completion: Future[CleanupResult] = Future()
            self._cleanup_future = completion
            active = list(self._active.values())
            for token, future in active:
                token.cancel()
                if future is not None:
                    future.cancel()
            try:
                cleanup_task = self._pool.submit(self._close_owners_worker)
            except RuntimeError:
                cleanup_task = Future()
                cleanup_task.set_result(0)
            self._pool.shutdown(wait=False, cancel_futures=False)

        def wait_for_cleanup() -> None:
            owners_closed = 0
            try:
                self._pool.shutdown(wait=True, cancel_futures=False)
                owners_closed = int(cleanup_task.result())
                for host in tuple(self._metadata_hosts.values()):
                    try:
                        host.close_async().result()
                        owners_closed += 1
                    except Exception:
                        # Completion still waits for each Future; close_async
                        # resolves after actual worker shutdown, even on error.
                        pass
                with self._lock:
                    self._nodes.clear()
                    self._node_bytes = 0
                    self._roots.clear()
                    self._cursors.clear()
                    self._cursor_bytes = 0
                    self._pages.clear()
                    self._page_bytes = 0
                    self._metadata_hosts.clear()
                    self._nosql_factories.clear()
                    self._nosql_leases.clear()
                    self._active.clear()
                completion.set_result(CleanupResult(True, owners_closed))
            except Exception:
                completion.set_exception(ObjectTreeError("QUERY_FAILED", "object tree cleanup failed"))

        threading.Thread(target=wait_for_cleanup, name="data-center-object-tree-close", daemon=True).start()
        return completion

    def _submit(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        cursor: str | None,
        refresh: bool,
        generation: int,
        page_size: int | None,
        details: bool,
    ) -> ObjectTreeRequest:
        if not isinstance(target, TreeTarget):
            raise TypeError("target must be a TreeTarget")
        if not isinstance(node, TreeNode):
            raise TypeError("node must be a TreeNode")
        if not isinstance(refresh, bool):
            raise ValueError("refresh must be a boolean")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        selected_page_size = self._page_size if page_size is None else page_size
        if isinstance(selected_page_size, bool) or not isinstance(selected_page_size, int) or not 1 <= selected_page_size <= self._page_size:
            raise ValueError("page_size is out of range")
        token_value = _bounded_cursor(cursor)
        if refresh and token_value:
            return _failed_request(ObjectTreeError("ARGUMENT_INVALID", "refresh cannot reuse a page cursor"))
        with self._lock:
            if self._closed:
                return _failed_request(ObjectTreeError("SESSION_NOT_FOUND", "object tree service is closed"))
            self._prune_locked()
            try:
                record = self._resolve_node_locked(target, node)
            except ObjectTreeError as exc:
                return _failed_request(exc)
            cursor_record: _CursorRecord | None = None
            if token_value:
                try:
                    cursor_record = self._resolve_cursor_locked(target, node, token_value)
                except ObjectTreeError as exc:
                    return _failed_request(exc)
                if cursor_record.consumed or cursor_record.reserved:
                    return _failed_request(ObjectTreeError("STALE_CURSOR", "object tree cursor was already used"))
            parent_key = (target.scope_key, node.node_ref)
            previous_generation, _previous_sequence = self._latest_requests.get(parent_key, (-1, -1))
            if generation < previous_generation:
                return _failed_request(ObjectTreeError("STALE_NODE", "object tree generation is stale"))
            if refresh:
                self._bump_epoch_locked(target, node.node_ref)
                self._drop_descendants_locked(target, node.node_ref)
                self._drop_sibling_cursors_locked(target, node.node_ref, keep=token_value)
            epoch = self._parent_epochs.get(parent_key, 0)
            self._request_seq += 1
            sequence = self._request_seq
            self._latest_requests[parent_key] = (generation, sequence)
            if cursor_record is not None:
                cursor_record.reserved = True

            cache_key = (target.scope_key, node.node_ref, token_value)
            cached = self._get_cached_page_locked(cache_key, epoch, generation)
            if cached is not None and not refresh and not details:
                if cursor_record is not None:
                    cursor_record.reserved = False
                    cursor_record.consumed = True
                return _completed_request(cached)

            if not details and not record.actionable:
                page = TreePage(target, node.node_ref, (), None, generation, epoch, self._clock(), "fresh", notices=(record_notice(record),))
                self._cache_page_locked(cache_key, page)
                return _completed_request(page)

            if not self._slots.acquire(blocking=False):
                if cursor_record is not None:
                    cursor_record.reserved = False
                return _failed_request(ObjectTreeError("CAPABILITY_UNAVAILABLE", "object tree request queue is full"))
            request_id = "tree_req_" + secrets.token_urlsafe(18)
            cancellation = _Cancellation()
            try:
                future = self._pool.submit(
                    self._execute_request,
                    request_id,
                    cancellation,
                    target,
                    node,
                    record,
                    cursor_record,
                    token_value,
                    refresh,
                    generation,
                    sequence,
                    epoch,
                    selected_page_size,
                    details,
                )
            except RuntimeError:
                self._slots.release()
                if cursor_record is not None:
                    cursor_record.reserved = False
                return _failed_request(ObjectTreeError("SESSION_NOT_FOUND", "object tree service is closed"))
            self._active[request_id] = (cancellation, future)
            future.add_done_callback(lambda done, request_id=request_id: self._request_done(request_id, done))
            return ObjectTreeRequest(request_id, future, cancellation)

    def _execute_request(
        self,
        request_id: str,
        cancellation: _Cancellation,
        target: TreeTarget,
        node: TreeNode,
        record: _NodeRecord,
        cursor_record: _CursorRecord | None,
        external_cursor: str,
        refresh: bool,
        generation: int,
        sequence: int,
        refresh_epoch: int,
        page_size: int,
        details: bool,
    ) -> TreePage | TreeNodeDetails:
        del request_id, refresh
        if cancellation.is_cancelled():
            self._release_cursor(cursor_record, consumed=False)
            raise ObjectTreeError("CANCELLED", "object tree request cancelled")
        try:
            if details:
                result = self._load_node_details(target, node, record, cancellation, generation)
                if cancellation.is_cancelled():
                    raise ObjectTreeError("CANCELLED", "object tree request cancelled")
                with self._lock:
                    self._release_cursor_locked(cursor_record, consumed=False)
                    parent_key = (target.scope_key, node.node_ref)
                    latest = self._latest_requests.get(parent_key)
                    if self._closed or latest != (generation, sequence) or self._nodes.get(node.node_ref) is not record:
                        raise ObjectTreeError("STALE_NODE", "object tree result is stale")
                return result

            raw_entries, backend_cursor, backend_kind, notices, capability_notice = self._load_children(
                target,
                record,
                cursor_record,
                page_size,
                cancellation,
            )
            if cancellation.is_cancelled():
                raise ObjectTreeError("CANCELLED", "object tree request cancelled")
            parent_key = (target.scope_key, node.node_ref)
            cache_key = (target.scope_key, node.node_ref, external_cursor)
            with self._lock:
                self._release_cursor_locked(cursor_record, consumed=cursor_record is not None)
                latest = self._latest_requests.get(parent_key)
                current_epoch = self._parent_epochs.get(parent_key, 0)
                stale = (
                    self._closed
                    or latest != (generation, sequence)
                    or current_epoch != refresh_epoch
                    or self._nodes.get(node.node_ref) is not record
                )
                if stale:
                    return TreePage(
                        target,
                        node.node_ref,
                        (),
                        None,
                        generation,
                        refresh_epoch,
                        self._clock(),
                        "stale",
                        capability_notice,
                        tuple(notices),
                    )
                child_nodes: list[TreeNode] = []
                page_notices = set(notices)
                for entry in raw_entries:
                    child = self._issue_entry_locked(
                        target, node, record, entry, generation=generation, refresh_epoch=refresh_epoch
                    )
                    if child is None:
                        page_notices.add("tree_node_registry_budget_exhausted")
                    else:
                        child_nodes.append(child)
                        if child.notice:
                            page_notices.add(child.notice)
                live_children = [child for child in child_nodes if child.node_ref in self._nodes]
                if len(live_children) != len(child_nodes):
                    page_notices.add("tree_node_registry_budget_exhausted")
                child_nodes = live_children
                next_token = self._issue_cursor_locked(
                    target,
                    node,
                    refresh_epoch,
                    backend_kind,
                    backend_cursor,
                ) if backend_cursor is not None else None
                if backend_cursor is not None and next_token is None:
                    page_notices.add("tree_cursor_registry_budget_exhausted")
                page = TreePage(
                    target,
                    node.node_ref,
                    tuple(child_nodes),
                    next_token,
                    generation,
                    refresh_epoch,
                    self._clock(),
                    "fresh",
                    capability_notice,
                    tuple(sorted(page_notices)),
                )
                self._cache_page_locked(cache_key, page)
                return page
        except Exception as exc:
            with self._lock:
                self._release_cursor_locked(cursor_record, consumed=False)
            if isinstance(exc, ObjectTreeError):
                raise
            code = _safe_code(getattr(exc, "code", "QUERY_FAILED"))
            raise ObjectTreeError(code, "object tree metadata request failed") from None

    def _load_children(
        self,
        target: TreeTarget,
        record: _NodeRecord,
        cursor_record: _CursorRecord | None,
        page_size: int,
        cancellation: _Cancellation,
    ) -> tuple[list[dict[str, Any]], Any, str, list[str], str]:
        provider = target.provider
        if record.kind in {"table", "view", "redis_key", "mongo_collection"}:
            return [], None, "none", [], ""
        if provider == "dameng":
            return [], None, "none", ["dameng_directory_unavailable_readonly_connector_not_wired"], "目录浏览暂不可用：达梦关系库目录模板尚未接线。"
        if provider not in _KNOWN_PROVIDERS:
            return [], None, "none", ["provider_directory_unavailable"], "当前连接类型尚无对象目录适配器。"
        if provider in {"oracle", "mysql", "oceanbase_oracle", "oceanbase_mysql"}:
            if record.kind in {"table", "view"}:
                return [], None, "none", [], ""
            host = self._metadata_host(target)
            if provider in {"mysql", "oceanbase_mysql"}:
                parent_kind = "root" if record.kind == "root" else "database"
            else:
                parent_kind = "root" if record.kind == "root" else "schema"
            result = host.directory_children(
                parent_kind,
                database=record.database,
                schema=record.schema,
                offset=int(cursor_record.backend_cursor) if cursor_record and cursor_record.backend_kind == "offset" else 0,
                page_size=page_size,
                cancellation=cancellation,
            )
            if not result.ok:
                raise ObjectTreeError(result.code, "database directory request failed")
            data = result.data if isinstance(result.data, Mapping) else {}
            entries = [item for item in data.get("entries", ()) if isinstance(item, Mapping)]
            next_offset = data.get("next_offset")
            if next_offset is not None and (isinstance(next_offset, bool) or not isinstance(next_offset, int)):
                raise ObjectTreeError("QUERY_FAILED", "database directory cursor is invalid")
            normalized: list[dict[str, Any]] = []
            for item in entries[:page_size]:
                kind = str(item.get("kind", ""))
                if kind not in {"database", "schema", "table", "view"}:
                    continue
                identity = item.get("name")
                name = _directory_name(identity)
                if name is None:
                    continue
                if not isinstance(identity, str):
                    continue
                normalized.append({"kind": kind, "name": name, "identity": identity, "actionable": True})
            return normalized, next_offset, "offset", [], ""
        if provider in {"redis", "mongodb"}:
            lease, database = self._nosql_lease(target, record.database, cancellation)
            if provider == "redis":
                parent_kind = "redis_root" if record.kind == "root" else "redis_key"
                page = lease._directory_children(
                    parent_kind,
                    prefix=record.prefix,
                    cursor=cursor_record.backend_cursor if cursor_record and cursor_record.backend_kind == "nosql" else None,
                    limit=page_size,
                    cancellation=cancellation,
                )
            else:
                parent_kind = "mongo_root" if record.kind == "root" else "mongo_database"
                page = lease._directory_children(
                    parent_kind,
                    database=record.database,
                    cursor=cursor_record.backend_cursor if cursor_record and cursor_record.backend_kind == "nosql" else None,
                    limit=page_size,
                    cancellation=cancellation,
                )
            self._verify_nosql_target(target)
            normalized = []
            for item in page.entries:
                kind = str(item.kind)
                if provider == "redis" and kind != "redis_key":
                    continue
                if provider == "mongodb" and kind not in {"mongo_database", "mongo_collection"}:
                    continue
                name = _directory_name(item.name)
                if name is None:
                    continue
                identity = item.identity
                if isinstance(identity, bytes) and len(identity) > MAX_TREE_NODE_IDENTITY_BYTES:
                    identity = None
                elif isinstance(identity, str) and len(identity.encode("utf-8", "strict")) > MAX_TREE_NODE_IDENTITY_BYTES:
                    identity = None
                actionable = identity is not None
                notice = str(item.notice or "")
                if not actionable and not notice:
                    notice = "directory_identity_unavailable"
                if provider == "redis" and isinstance(identity, bytes) and not identity.startswith(record.prefix.encode("utf-8", "strict")):
                    continue
                database_name = identity if kind == "mongo_database" and isinstance(identity, str) else record.database
                normalized.append(
                    {
                        "kind": kind,
                        "name": name,
                        "identity": identity,
                        "database": database_name,
                        "actionable": actionable,
                        "notice": notice,
                    }
                )
            return normalized, page.next_cursor, "nosql", list(page.notices), ""
        return [], None, "none", ["provider_directory_unavailable"], "当前连接类型尚无对象目录适配器。"

    def _load_node_details(
        self,
        target: TreeTarget,
        node: TreeNode,
        record: _NodeRecord,
        cancellation: _Cancellation,
        generation: int,
    ) -> TreeNodeDetails:
        if target.provider != "redis" or record.kind != "redis_key" or not isinstance(record.identity, bytes):
            raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "该节点没有可读取的类型或 TTL 元数据")
        if not record.identity.startswith(record.prefix.encode("utf-8", "strict")):
            raise ObjectTreeError("SCOPE_DENIED", "Redis key is outside the signed prefix")
        lease, _database = self._nosql_lease(target, "", cancellation)
        value = lease._directory_inspect_key(record.identity, cancellation=cancellation)
        self._verify_nosql_target(target)
        if cancellation.is_cancelled():
            raise ObjectTreeError("CANCELLED", "object tree request cancelled")
        value_type = str(value.get("value_type", "unknown"))[:64]
        ttl = value.get("ttl_seconds")
        if isinstance(ttl, bool) or not isinstance(ttl, int):
            raise ObjectTreeError("QUERY_FAILED", "Redis TTL metadata is invalid")
        return TreeNodeDetails(target, node.node_ref, value_type, ttl, generation)

    def _metadata_host(self, target: TreeTarget) -> MetadataHost:
        key = target.scope_key
        with self._lock:
            host = self._metadata_hosts.get(key)
            if host is not None:
                self._metadata_hosts.move_to_end(key)
                return host
            if self._relational_host is None:
                raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "relational metadata host is unavailable")
            if len(self._metadata_hosts) >= MAX_METADATA_HOSTS:
                raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "too many metadata targets are active")
            try:
                metadata_target = MetadataTarget(
                    target.connection_id,
                    target.profile_revision,
                    target.provider,
                    target.mode,
                    target.database,
                    (),
                )
                host = MetadataHost(
                    metadata_target,
                    relational_host=self._relational_host,
                    profile_snapshot=getattr(self._relational_host, "profile_snapshot", None),
                    max_page_size=MAX_METADATA_PAGE,
                )
            except Exception:
                raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "relational metadata host could not be created") from None
            self._metadata_hosts[key] = host
            return host

    def _nosql_lease(
        self,
        target: TreeTarget,
        database: str,
        cancellation: _Cancellation,
    ) -> tuple[Any, str]:
        if self._nosql_host is None:
            raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "NoSQL metadata host is unavailable")
        if cancellation.is_cancelled():
            raise ObjectTreeError("CANCELLED", "object tree request cancelled")
        snapshot = self._verify_nosql_target(target)
        # The lease is bound to the selected profile once per target. Mongo
        # database names are subsequently supplied only by signed tree nodes
        # to its metadata-only directory method, so browsing many databases
        # does not create an unbounded collection of live clients.
        selected_database = target.database or str(getattr(snapshot, "database_name", "") or "")
        key = (target.scope_key, selected_database)
        lease = self._nosql_leases.get(key)
        if lease is not None and not getattr(lease, "closed", False):
            return lease, selected_database
        try:
            factory = self._nosql_factories.get(key)
            if factory is None:
                if len(self._nosql_factories) >= MAX_NOSQL_TARGETS:
                    raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "too many NoSQL directory targets are active")
                factory = self._nosql_host.create_directory_factory(
                    target.connection_id,
                    profile_revision=target.profile_revision,
                    provider=target.provider,
                    mode=target.mode,
                    database_name=selected_database,
                )
                self._nosql_factories[key] = factory
            lease = factory(target.connection_id)
        except Exception as exc:
            code = _safe_code(getattr(exc, "code", "CONNECT_FAILED"))
            raise ObjectTreeError(code, "NoSQL metadata lease is unavailable") from None
        try:
            # Profile/revision can drift while a factory is opening the client.
            # Until these post-open checks pass, the lease is not registered,
            # so every failure must close it here on this owner worker.
            self._verify_nosql_target(target)
            if not bool(getattr(lease, "_directory_metadata", False)):
                raise ObjectTreeError("SCOPE_DENIED", "NoSQL lease is not a directory-only lease")
        except Exception as exc:
            try:
                lease.close()
            except Exception:
                pass
            if isinstance(exc, ObjectTreeError):
                raise
            code = _safe_code(getattr(exc, "code", "CONNECT_FAILED"))
            raise ObjectTreeError(code, "NoSQL metadata lease is unavailable") from None
        self._nosql_leases[key] = lease
        return lease, selected_database

    def _verify_nosql_target(self, target: TreeTarget) -> Any:
        try:
            snapshot = self._nosql_host.profile_snapshot(target.connection_id)
        except Exception:
            raise ObjectTreeError("PROFILE_UNAVAILABLE", "NoSQL profile snapshot is unavailable") from None
        provider = str(getattr(snapshot, "provider", ""))
        mode = str(getattr(snapshot, "mode", ""))
        if (
            str(getattr(snapshot, "connection_id", "")) != target.connection_id
            or str(getattr(snapshot, "profile_revision", "")) != target.profile_revision
            or provider != target.provider
            or mode != target.mode
        ):
            raise ObjectTreeError("PROFILE_MISMATCH", "NoSQL target changed while the directory was open")
        return snapshot

    def _issue_entry_locked(
        self,
        target: TreeTarget,
        parent: TreeNode,
        parent_record: _NodeRecord,
        entry: Mapping[str, Any],
        *,
        generation: int,
        refresh_epoch: int,
    ) -> TreeNode | None:
        if self._nodes.get(parent.node_ref) is not parent_record:
            return None
        self._nodes.move_to_end(parent.node_ref)
        kind = str(entry.get("kind", ""))
        name = _directory_name(entry.get("name"))
        if name is None or kind not in {"database", "schema", "table", "view", "redis_key", "mongo_database", "mongo_collection"}:
            return None
        identity = entry.get("identity")
        actionable = bool(entry.get("actionable", identity is not None))
        notice = str(entry.get("notice", ""))[:128]
        if isinstance(identity, bytes):
            if len(identity) > MAX_TREE_NODE_IDENTITY_BYTES:
                identity = None
                actionable = False
                notice = notice or "directory_identity_over_budget"
        elif isinstance(identity, str):
            try:
                if len(identity.encode("utf-8", "strict")) > MAX_TREE_NODE_IDENTITY_BYTES:
                    identity = None
                    actionable = False
                    notice = notice or "directory_identity_over_budget"
            except UnicodeError:
                identity = None
                actionable = False
                notice = notice or "directory_identity_unrepresentable"
        elif identity is not None:
            return None
        if actionable and identity is None:
            actionable = False
            notice = notice or "directory_identity_unavailable"
        database = str(entry.get("database", parent_record.database) or "")
        schema = str(entry.get("schema", parent_record.schema) or "")
        if kind == "database":
            database = identity if isinstance(identity, str) else name
            schema = ""
        elif kind == "schema":
            schema = identity if isinstance(identity, str) else name
        elif kind in {"table", "view"} and not schema and parent_record.kind == "database":
            database = parent_record.database
        if kind == "mongo_database" and isinstance(identity, str):
            database = identity
        if target.provider == "redis" and isinstance(identity, bytes):
            try:
                if not identity.startswith(parent_record.prefix.encode("utf-8", "strict")):
                    return None
            except UnicodeError:
                return None
        return self._issue_node_locked(
            target,
            kind=kind,
            name=name,
            database=database,
            schema=schema,
            parent_ref=parent.node_ref,
            prefix=parent_record.prefix,
            identity=identity,
            actionable=actionable,
            notice=notice,
            generation=generation,
            refresh_epoch=refresh_epoch,
            protected_ref=parent.node_ref,
        )

    def _issue_node_locked(
        self,
        target: TreeTarget,
        *,
        kind: str,
        name: str,
        database: str,
        schema: str,
        parent_ref: str,
        prefix: str,
        identity: bytes | str | None,
        actionable: bool,
        notice: str = "",
        generation: int = 0,
        refresh_epoch: int = 0,
        protected_ref: str = "",
    ) -> TreeNode | None:
        now = self._clock()
        self._prune_nodes_locked(now)
        identity_bytes = len(identity) if isinstance(identity, bytes) else len(identity.encode("utf-8", "strict")) if isinstance(identity, str) else 0
        cost = (
            160
            + len(name.encode("utf-8", "replace"))
            + len(database.encode("utf-8", "replace"))
            + len(schema.encode("utf-8", "replace"))
            + len(prefix.encode("utf-8", "replace"))
            + identity_bytes
        )
        if cost > MAX_NODE_REGISTRY_BYTES:
            return None
        while self._nodes and (len(self._nodes) >= MAX_NODE_ENTRIES or self._node_bytes + cost > MAX_NODE_REGISTRY_BYTES):
            oldest_ref = next((ref for ref in self._nodes if ref != protected_ref), "")
            if not oldest_ref:
                return None
            self._drop_node_locked(oldest_ref)
        if len(self._nodes) >= MAX_NODE_ENTRIES or self._node_bytes + cost > MAX_NODE_REGISTRY_BYTES:
            return None
        node_ref = "tree_node_" + secrets.token_urlsafe(24)
        record = _NodeRecord(
            target.scope_key,
            kind,
            name,
            database,
            schema,
            parent_ref,
            prefix,
            identity,
            bool(actionable),
            notice,
            generation,
            refresh_epoch,
            now + self._node_ttl,
            cost,
        )
        self._nodes[node_ref] = record
        self._node_bytes += cost
        return TreeNode(node_ref, kind, name, database, schema, parent_ref, bool(actionable), notice)

    def resolve_table_source(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        generation: int,
    ) -> VerifiedTableRef:
        """Resolve a signed table/view node without opening a host or DB lease."""

        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if not isinstance(target, TreeTarget) or not isinstance(node, TreeNode):
            raise TypeError("target and node must be signed tree values")
        with self._lock:
            if self._closed:
                raise ObjectTreeError("SESSION_NOT_FOUND", "object tree service is closed")
            self._prune_nodes_locked(self._clock())
            record = self._resolve_node_locked(target, node)
            if record.kind not in {"table", "view"}:
                raise ObjectTreeError("SCOPE_DENIED", "object tree node is not a relational table or view")
            if not record.actionable or not isinstance(record.identity, str) or not record.identity:
                raise ObjectTreeError("SCOPE_DENIED", "object tree node has no verified raw name")
            if record.generation != generation:
                raise ObjectTreeError("STALE_NODE", "object tree generation changed")
            parent_epoch = self._parent_epochs.get((target.scope_key, record.parent_ref), 0)
            if record.refresh_epoch != parent_epoch:
                raise ObjectTreeError("STALE_NODE", "object tree node was invalidated by refresh")
            return VerifiedTableRef(
                target=target,
                node_ref=node.node_ref,
                tree_generation=record.generation,
                refresh_epoch=record.refresh_epoch,
                kind=record.kind,
                raw_name=record.identity,
                database=record.database,
                schema=record.schema,
            )

    def _resolve_node_locked(self, target: TreeTarget, node: TreeNode) -> _NodeRecord:
        record = self._nodes.get(node.node_ref)
        if record is None or record.expires_at <= self._clock():
            if record is not None:
                self._drop_node_locked(node.node_ref)
            raise ObjectTreeError("STALE_NODE", "object tree node reference is expired")
        if record.target_key != target.scope_key:
            raise ObjectTreeError("SCOPE_DENIED", "object tree node belongs to another target")
        if (
            node.kind != record.kind
            or node.name != record.name
            or node.database != record.database
            or node.schema != record.schema
            or node.parent_ref != record.parent_ref
            or node.actionable != record.actionable
            or node.notice != record.notice
        ):
            raise ObjectTreeError("SCOPE_DENIED", "object tree node fields do not match its issued reference")
        self._nodes.move_to_end(node.node_ref)
        return record

    def _resolve_cursor_locked(self, target: TreeTarget, node: TreeNode, token: str) -> _CursorRecord:
        record = self._cursors.get(token)
        if record is None or record.expires_at <= self._clock():
            if record is not None:
                self._drop_cursor_locked(token)
            raise ObjectTreeError("STALE_CURSOR", "object tree cursor is expired")
        if record.target_key != target.scope_key or record.parent_ref != node.node_ref:
            raise ObjectTreeError("SCOPE_DENIED", "object tree cursor belongs to another path")
        parent_epoch = self._parent_epochs.get((target.scope_key, node.node_ref), 0)
        if record.refresh_epoch != parent_epoch:
            raise ObjectTreeError("STALE_CURSOR", "object tree cursor was invalidated by refresh")
        return record

    def _issue_cursor_locked(self, target: TreeTarget, node: TreeNode, epoch: int, kind: str, cursor: Any) -> str | None:
        token_bytes = len(str(cursor).encode("utf-8", "replace"))
        cost = 128 + token_bytes
        if cursor is None or cost > MAX_CURSOR_REGISTRY_BYTES:
            return None
        self._prune_cursors_locked()
        while self._cursors and (len(self._cursors) >= MAX_CURSOR_ENTRIES or self._cursor_bytes + cost > MAX_CURSOR_REGISTRY_BYTES):
            self._drop_cursor_locked(next(iter(self._cursors)))
        if len(self._cursors) >= MAX_CURSOR_ENTRIES or self._cursor_bytes + cost > MAX_CURSOR_REGISTRY_BYTES:
            return None
        token = "tree_page_" + secrets.token_urlsafe(24)
        self._cursors[token] = _CursorRecord(
            target.scope_key,
            node.node_ref,
            epoch,
            kind,
            cursor,
            self._clock() + self._cache_ttl,
            cost,
        )
        self._cursor_bytes += cost
        return token

    def _get_cached_page_locked(self, key: tuple[Any, ...], epoch: int, generation: int) -> TreePage | None:
        record = self._pages.get(key)
        if record is None:
            return None
        if record.expires_at <= self._clock() or any(node.node_ref not in self._nodes for node in record.page.nodes):
            self._drop_page_locked(key)
            return None
        self._pages.move_to_end(key)
        page = record.page
        cursor = page.next_cursor
        cursor_record = self._cursors.get(cursor or "")
        if cursor and (cursor_record is None or cursor_record.consumed or cursor_record.expires_at <= self._clock()):
            self._drop_page_locked(key)
            return None
        return replace(
            page,
            next_cursor=cursor,
            generation=generation,
            refresh_epoch=epoch,
            cache_state="stale" if page.refresh_epoch != epoch else "cached",
        )

    def _cache_page_locked(self, key: tuple[Any, ...], page: TreePage) -> None:
        old = self._pages.pop(key, None)
        if old is not None:
            self._page_bytes -= old.byte_cost
        cost = 192 + len(page.parent_ref) + len(page.capability_notice.encode("utf-8", "replace"))
        cost += sum(
            128 + len(node.node_ref) + len(node.name.encode("utf-8", "replace"))
            + len(node.database.encode("utf-8", "replace"))
            + len(node.schema.encode("utf-8", "replace"))
            + len(node.notice.encode("utf-8", "replace"))
            for node in page.nodes
        )
        if cost > MAX_PAGE_CACHE_BYTES:
            return
        while self._pages and (len(self._pages) >= MAX_PAGE_CACHE_ENTRIES or self._page_bytes + cost > MAX_PAGE_CACHE_BYTES):
            self._drop_page_locked(next(iter(self._pages)))
        self._pages[key] = _PageCacheRecord(page, self._clock() + self._cache_ttl, cost)
        self._page_bytes += cost

    def _request_done(self, request_id: str, future: Future[Any]) -> None:
        del future
        with self._lock:
            self._active.pop(request_id, None)
        try:
            self._slots.release()
        except ValueError:
            pass

    def _close_owners_worker(self) -> int:
        owners_closed = 0
        for lease in tuple(self._nosql_leases.values()):
            try:
                lease.close()
                owners_closed += 1
            except Exception:
                pass
        for factory in tuple(self._nosql_factories.values()):
            try:
                factory.close()
            except Exception:
                pass
        self._nosql_leases.clear()
        self._nosql_factories.clear()
        return owners_closed

    def _release_cursor(self, record: _CursorRecord | None, *, consumed: bool) -> None:
        with self._lock:
            self._release_cursor_locked(record, consumed=consumed)

    def _release_cursor_locked(self, record: _CursorRecord | None, *, consumed: bool) -> None:
        if record is None:
            return
        record.reserved = False
        record.consumed = bool(consumed)
        if consumed:
            for token, candidate in list(self._cursors.items()):
                if candidate is record:
                    self._drop_cursor_locked(token)
                    break

    def _bump_epoch_locked(self, target: TreeTarget, parent_ref: str) -> int:
        key = (target.scope_key, parent_ref)
        epoch = self._parent_epochs.get(key, 0) + 1
        self._parent_epochs[key] = epoch
        return epoch

    def _drop_sibling_cursors_locked(self, target: TreeTarget, parent_ref: str, *, keep: str) -> None:
        for token, record in list(self._cursors.items()):
            if record.target_key == target.scope_key and record.parent_ref == parent_ref and token != keep:
                self._drop_cursor_locked(token)

    def _drop_descendants_locked(self, target: TreeTarget, parent_ref: str) -> None:
        refs = {parent_ref}
        changed = True
        while changed:
            changed = False
            for ref, record in list(self._nodes.items()):
                if record.target_key == target.scope_key and record.parent_ref in refs and ref not in refs:
                    refs.add(ref)
                    changed = True
        for ref in refs - {parent_ref}:
            self._drop_node_locked(ref)
        for key in list(self._pages):
            if key[0] == target.scope_key and key[1] == parent_ref:
                self._drop_page_locked(key)

    def _drop_node_locked(self, ref: str) -> None:
        # References are path capabilities. Evicting a parent also invalidates
        # every descendant so an old child cannot outlive its issued path.
        for child_ref, child in list(self._nodes.items()):
            if child.parent_ref == ref:
                self._drop_node_locked(child_ref)
        record = self._nodes.pop(ref, None)
        if record is not None:
            self._node_bytes = max(0, self._node_bytes - record.byte_cost)
            key = (record.target_key, ref)
            self._parent_epochs.pop(key, None)
            self._latest_requests.pop(key, None)
            for token, cursor_record in list(self._cursors.items()):
                if cursor_record.parent_ref == ref:
                    self._drop_cursor_locked(token)
            for page_key, page_record in list(self._pages.items()):
                if page_key[1] == ref or any(node.node_ref == ref for node in page_record.page.nodes):
                    self._drop_page_locked(page_key)
        for key, value in list(self._roots.items()):
            if value == ref:
                self._roots.pop(key, None)

    def _drop_cursor_locked(self, token: str) -> None:
        record = self._cursors.pop(token, None)
        if record is not None:
            self._cursor_bytes = max(0, self._cursor_bytes - record.byte_cost)

    def _drop_page_locked(self, key: tuple[Any, ...]) -> None:
        record = self._pages.pop(key, None)
        if record is not None:
            self._page_bytes = max(0, self._page_bytes - record.byte_cost)

    def _prune_nodes_locked(self, now: float | None = None) -> None:
        current = self._clock() if now is None else now
        for ref, record in list(self._nodes.items()):
            if record.expires_at <= current:
                self._drop_node_locked(ref)

    def _prune_cursors_locked(self) -> None:
        current = self._clock()
        for token, record in list(self._cursors.items()):
            if record.expires_at <= current:
                self._drop_cursor_locked(token)

    def _prune_locked(self) -> None:
        now = self._clock()
        self._prune_nodes_locked(now)
        self._prune_cursors_locked()
        for key, record in list(self._pages.items()):
            if record.expires_at <= now:
                self._drop_page_locked(key)
        # These per-parent maps are bounded by the current node registry.
        live = set(self._nodes)
        self._parent_epochs = {key: value for key, value in self._parent_epochs.items() if key[1] in live}
        self._latest_requests = {key: value for key, value in self._latest_requests.items() if key[1] in live}


def _scope_text(value: Any, name: str, *, required: bool = False, trim: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    text = value.strip() if trim else value
    if required and not text:
        raise ValueError(f"{name} must be non-empty")
    if len(text.encode("utf-8", "strict")) > 1024 or "\x00" in text:
        raise ValueError(f"{name} is invalid")
    return text


def _directory_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    try:
        raw = value.encode("utf-8", "strict")
    except UnicodeError:
        return None
    if len(raw) > MAX_TREE_NAME_BYTES:
        return None
    return "".join(character for character in value if character not in {"\x00", "\r", "\n"})[:256]


def _bounded_cursor(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > 256 or "\x00" in value:
        raise ObjectTreeError("ARGUMENT_INVALID", "object tree cursor is invalid")
    return value


def _safe_code(value: Any) -> str:
    code = str(value or "").upper()
    return code if code in _SAFE_ERROR_CODES else "QUERY_FAILED"


def record_notice(record: _NodeRecord) -> str:
    return record.notice


def _public_node(ref: str, record: _NodeRecord) -> TreeNode:
    return TreeNode(ref, record.kind, record.name, record.database, record.schema, record.parent_ref, record.actionable, record.notice)


def _completed_request(value: Any) -> ObjectTreeRequest:
    future: Future[Any] = Future()
    future.set_result(value)
    return ObjectTreeRequest("tree_cached_" + secrets.token_urlsafe(12), future, _Cancellation())


def _failed_request(error: BaseException) -> ObjectTreeRequest:
    future: Future[Any] = Future()
    future.set_exception(error)
    return ObjectTreeRequest("tree_rejected_" + secrets.token_urlsafe(12), future, _Cancellation())


__all__ = [
    "CleanupResult",
    "ObjectTreeError",
    "ObjectTreeRequest",
    "ObjectTreeService",
    "TreeNode",
    "TreeNodeDetails",
    "TreePage",
    "TreeTarget",
]
