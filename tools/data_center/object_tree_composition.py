"""Lazy application composition for the user-only Data Center object tree."""

from __future__ import annotations

from concurrent.futures import Future, InvalidStateError, ThreadPoolExecutor
import threading
from typing import Any

from .host_nosql import NoSQLHostError, NoSQLProfileSnapshot, NoSQLReadOnlyHost
from .host_relational import (
    RelationalHostError,
    RelationalProfileSnapshot,
    RelationalReadOnlyHost,
)
from .object_tree import CleanupResult, ObjectTreeError, ObjectTreeService, TreeTarget


MAX_CAPTURE_PENDING = 16
_ROUTE_MISS_CODES = frozenset(
    {"PROFILE_NOT_FOUND", "UNSUPPORTED_COMBINATION", "UNSUPPORTED_PROVIDER"}
)


class _CaptureToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._finished = False

    def cancel(self) -> bool:
        with self._lock:
            if self._finished:
                return False
            self._event.set()
            return True

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def finish(
        self,
        future: Future[TreeTarget],
        *,
        target: TreeTarget | None = None,
        error: Exception | None = None,
    ) -> None:
        """Atomically publish a result only if cancellation did not win."""

        with self._lock:
            if self._finished:
                return
            self._finished = True
            cancelled = self._event.is_set()
        # Future completion invokes callbacks synchronously. Publish outside
        # the token lock so a callback can safely request composition close.
        try:
            if future.done():
                return
            if cancelled:
                future.set_exception(ObjectTreeError("CANCELLED", "profile capture cancelled"))
            elif error is not None:
                future.set_exception(error)
            elif target is not None:
                future.set_result(target)
            else:
                future.set_exception(
                    ObjectTreeError("PROFILE_UNAVAILABLE", "connection profile is unavailable")
                )
        except InvalidStateError:
            # A queued Future may have been cancelled between the lock-free
            # publication check and setter. Its cancellation already won.
            return


class _CaptureFuture(Future[TreeTarget]):
    """Future whose cancellation also reaches an in-flight profile read."""

    def __init__(self, token: _CaptureToken) -> None:
        super().__init__()
        self._token = token

    def cancel(self) -> bool:
        if not self._token.cancel():
            return False
        return super().cancel()


class ObjectTreeComposition:
    """Compose production-safe hosts without loading a profile at construction."""

    def __init__(
        self,
        *,
        relational_host: RelationalReadOnlyHost | None = None,
        nosql_host: NoSQLReadOnlyHost | None = None,
    ) -> None:
        self._relational_host = relational_host if relational_host is not None else RelationalReadOnlyHost()
        self._nosql_host = nosql_host if nosql_host is not None else NoSQLReadOnlyHost()
        self._service = ObjectTreeService(
            relational_host=self._relational_host,
            nosql_host=self._nosql_host,
        )
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="data-center-tree-capture")
        self._slots = threading.BoundedSemaphore(MAX_CAPTURE_PENDING)
        self._lock = threading.RLock()
        self._closed = False
        self._active: dict[Future[Any], tuple[_CaptureFuture, _CaptureToken]] = {}
        self._cleanup_future: Future[CleanupResult] | None = None

    @property
    def service(self) -> ObjectTreeService:
        """The shared ObjectTreeService used after target capture."""

        return self._service

    def submit_target_capture(
        self,
        connection_id: str,
        *,
        database_scope: str = "",
        redis_prefix: str | None = None,
    ) -> Future[TreeTarget]:
        """Resolve one safe profile snapshot on the bounded capture worker."""

        try:
            selected_id = _bounded_text(connection_id, "connection_id", required=True)
            database = _bounded_text(database_scope, "database_scope")
            prefix = None if redis_prefix is None else _bounded_text(redis_prefix, "redis_prefix")
        except ObjectTreeError as exc:
            return _failed_future(exc)

        with self._lock:
            if self._closed:
                return _failed_future(
                    ObjectTreeError("CAPABILITY_UNAVAILABLE", "object tree composition is closed")
                )
            if not self._slots.acquire(blocking=False):
                return _failed_future(
                    ObjectTreeError("CAPABILITY_UNAVAILABLE", "profile capture queue is full")
                )
            token = _CaptureToken()
            result: _CaptureFuture = _CaptureFuture(token)
            try:
                worker = self._pool.submit(
                    self._capture_worker,
                    result,
                    token,
                    selected_id,
                    database,
                    prefix,
                )
            except RuntimeError:
                self._slots.release()
                return _failed_future(
                    ObjectTreeError("CAPABILITY_UNAVAILABLE", "object tree composition is closed")
                )
            self._active[worker] = (result, token)
            worker.add_done_callback(self._capture_done)
            return result

    def close(self) -> Future[CleanupResult]:
        """Cancel captures and wait for capture and tree owner workers to exit."""

        with self._lock:
            if self._cleanup_future is not None:
                return self._cleanup_future
            self._closed = True
            completion: Future[CleanupResult] = Future()
            self._cleanup_future = completion
            active = list(self._active.values())
            for result, token in active:
                token.cancel()
                result.cancel()
            try:
                service_cleanup = self._service.close()
            except Exception:
                service_cleanup = None
            self._pool.shutdown(wait=False, cancel_futures=False)

        def wait_for_cleanup() -> None:
            try:
                # A profile loader may not support interruption.  Keep both
                # hosts and their loader references alive until it returns.
                self._pool.shutdown(wait=True, cancel_futures=False)
                if service_cleanup is None:
                    raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "object tree cleanup could not start")
                result = service_cleanup.result()
                if not isinstance(result, CleanupResult) or not result.completed:
                    raise ObjectTreeError("CAPABILITY_UNAVAILABLE", "object tree cleanup is incomplete")
                completion.set_result(result)
            except ObjectTreeError as exc:
                completion.set_exception(exc)
            except Exception:
                completion.set_exception(
                    ObjectTreeError("CAPABILITY_UNAVAILABLE", "object tree composition cleanup failed")
                )

        threading.Thread(
            target=wait_for_cleanup,
            name="data-center-tree-composition-close",
            daemon=True,
        ).start()
        return completion

    def _capture_worker(
        self,
        result: _CaptureFuture,
        token: _CaptureToken,
        connection_id: str,
        database_scope: str,
        redis_prefix: str | None,
    ) -> None:
        if not result.set_running_or_notify_cancel():
            return
        try:
            target = self._capture_target(
                token,
                connection_id,
                database_scope,
                redis_prefix,
            )
        except ObjectTreeError as exc:
            token.finish(result, error=exc)
        except Exception:
            token.finish(
                result,
                error=ObjectTreeError(
                    "PROFILE_UNAVAILABLE", "connection profile is unavailable"
                ),
            )
        else:
            token.finish(result, target=target)

    def _capture_target(
        self,
        token: _CaptureToken,
        connection_id: str,
        database_scope: str,
        redis_prefix: str | None,
    ) -> TreeTarget:
        if token.is_cancelled():
            raise ObjectTreeError("CANCELLED", "profile capture cancelled")
        snapshot = self._resolve_snapshot(token, connection_id)
        if token.is_cancelled():
            raise ObjectTreeError("CANCELLED", "profile capture cancelled")
        if isinstance(snapshot, RelationalProfileSnapshot):
            if redis_prefix is not None:
                raise ObjectTreeError("SCOPE_DENIED", "Redis prefix is not valid for this connection")
            database = database_scope
            if snapshot.provider in {"oracle", "oceanbase_oracle"}:
                try:
                    service = self._relational_host.capture_oracle_tree_service(
                        connection_id,
                        snapshot.profile_revision,
                        provider=snapshot.provider,
                        mode=snapshot.mode,
                        dialect=snapshot.dialect,
                    )
                except RelationalHostError as exc:
                    code = str(getattr(exc, "code", "") or "").strip().upper()
                    if code == "PROFILE_MISMATCH":
                        public_code = "PROFILE_MISMATCH"
                    elif code == "UNSUPPORTED_COMBINATION":
                        public_code = "UNSUPPORTED_PROVIDER"
                    else:
                        public_code = "PROFILE_UNAVAILABLE"
                    raise ObjectTreeError(
                        public_code, "connection profile is unavailable for this directory scope"
                    ) from None
                except Exception:
                    raise ObjectTreeError(
                        "PROFILE_UNAVAILABLE", "connection profile is unavailable for this directory scope"
                    ) from None
                if database_scope and database_scope != service:
                    raise ObjectTreeError("SCOPE_DENIED", "Oracle service scope does not match the connection profile")
                database = service
            try:
                return TreeTarget(
                    connection_id,
                    snapshot.profile_revision,
                    snapshot.provider,
                    mode=snapshot.mode,
                    database=database,
                )
            except (TypeError, ValueError):
                raise ObjectTreeError("ARGUMENT_INVALID", "database scope is invalid") from None

        provider = snapshot.provider
        if provider == "redis":
            if database_scope:
                raise ObjectTreeError("SCOPE_DENIED", "database scope is not valid for Redis")
            if redis_prefix is None:
                raise ObjectTreeError("ARGUMENT_INVALID", "Redis prefix must be explicitly supplied")
            try:
                return TreeTarget(
                    connection_id,
                    snapshot.profile_revision,
                    provider,
                    mode=snapshot.mode,
                    redis_prefix=redis_prefix,
                )
            except (TypeError, ValueError):
                raise ObjectTreeError("ARGUMENT_INVALID", "Redis prefix is invalid") from None

        if provider == "mongodb":
            if redis_prefix is not None:
                raise ObjectTreeError("SCOPE_DENIED", "Redis prefix is not valid for MongoDB")
            # The public directory factory accepts an explicit database_name.
            # Empty input keeps the profile snapshot's database as context.
            selected_database = database_scope or snapshot.database_name
            if selected_database != selected_database.strip():
                raise ObjectTreeError("SCOPE_DENIED", "Mongo database scope cannot be represented exactly")
            try:
                return TreeTarget(
                    connection_id,
                    snapshot.profile_revision,
                    provider,
                    mode=snapshot.mode,
                    database=selected_database,
                )
            except (TypeError, ValueError):
                raise ObjectTreeError("ARGUMENT_INVALID", "Mongo database scope is invalid") from None

        raise ObjectTreeError("UNSUPPORTED_PROVIDER", "connection provider has no directory host")

    def _resolve_snapshot(
        self,
        token: _CaptureToken,
        connection_id: str,
    ) -> RelationalProfileSnapshot | NoSQLProfileSnapshot:
        misses: list[str] = []
        for host in (self._relational_host, self._nosql_host):
            if token.is_cancelled():
                raise ObjectTreeError("CANCELLED", "profile capture cancelled")
            try:
                snapshot = host.profile_snapshot(connection_id)
            except Exception as exc:
                code = _trusted_profile_error_code(host, exc)
                if code in _ROUTE_MISS_CODES:
                    misses.append(code)
                    continue
                if code:
                    raise ObjectTreeError(
                        _public_profile_error_code(code),
                        "connection profile is unavailable",
                    ) from None
                # Unknown loader/host failures are not a routing signal.
                raise ObjectTreeError(
                    "PROFILE_UNAVAILABLE",
                    "connection profile is unavailable",
                ) from None

            if isinstance(host, RelationalReadOnlyHost) and isinstance(snapshot, RelationalProfileSnapshot):
                if snapshot.connection_id != connection_id:
                    raise ObjectTreeError("PROFILE_MISMATCH", "connection profile identity changed")
                if token.is_cancelled():
                    raise ObjectTreeError("CANCELLED", "profile capture cancelled")
                return snapshot
            if isinstance(host, NoSQLReadOnlyHost) and isinstance(snapshot, NoSQLProfileSnapshot):
                if snapshot.connection_id != connection_id:
                    raise ObjectTreeError("PROFILE_MISMATCH", "connection profile identity changed")
                if token.is_cancelled():
                    raise ObjectTreeError("CANCELLED", "profile capture cancelled")
                return snapshot
            raise ObjectTreeError("PROFILE_UNAVAILABLE", "profile host returned an invalid snapshot")

        if any(code in {"UNSUPPORTED_COMBINATION", "UNSUPPORTED_PROVIDER"} for code in misses):
            raise ObjectTreeError("UNSUPPORTED_PROVIDER", "connection provider has no directory host")
        raise ObjectTreeError("PROFILE_UNAVAILABLE", "connection profile is unavailable")

    def _capture_done(self, worker: Future[Any]) -> None:
        with self._lock:
            if self._active.pop(worker, None) is None:
                return
        self._slots.release()


def _bounded_text(value: Any, name: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ObjectTreeError("ARGUMENT_INVALID", f"{name} must be text")
    text = value.strip() if name == "connection_id" else value
    if required and not text:
        raise ObjectTreeError("ARGUMENT_INVALID", f"{name} is required")
    try:
        size = len(text.encode("utf-8", "strict"))
    except UnicodeError:
        raise ObjectTreeError("ARGUMENT_INVALID", f"{name} is invalid") from None
    if size > 1024 or "\x00" in text:
        raise ObjectTreeError("ARGUMENT_INVALID", f"{name} is invalid")
    return text


def _trusted_profile_error_code(host: Any, exc: Exception) -> str:
    if isinstance(host, RelationalReadOnlyHost) and isinstance(exc, RelationalHostError):
        value = getattr(exc, "code", "")
    elif isinstance(host, NoSQLReadOnlyHost) and isinstance(exc, NoSQLHostError):
        value = getattr(exc, "code", "")
    else:
        return ""
    try:
        return str(value or "").strip().upper()
    except Exception:
        return ""


def _public_profile_error_code(code: str) -> str:
    if code in {"PROFILE_MISMATCH", "PROFILE_UNAVAILABLE", "UNSUPPORTED_PROVIDER"}:
        return code
    return "PROFILE_UNAVAILABLE"


def _failed_future(error: ObjectTreeError) -> Future[TreeTarget]:
    future: Future[TreeTarget] = Future()
    future.set_exception(error)
    return future


__all__ = ["MAX_CAPTURE_PENDING", "ObjectTreeComposition"]
