"""Versioned, lossless persistence for connection-profile JSON documents.

The caller supplies the path. This module deliberately has no dependency on
application config, database drivers, Qt, or secret decryption.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence


FORMAT_VERSION = 1
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_CONNECTIONS = 10_000
MAX_CONNECTION_BYTES = 4 * 1024 * 1024
MAX_CONNECTION_ID_CHARS = 1024
MAX_JSON_DEPTH = 64
MAX_REMOVE_FIELDS = 256
DEFAULT_LOCK_TIMEOUT_SECONDS = 0.75


class ConnectionStoreError(RuntimeError):
    """Stable, non-sensitive connection-store failure."""

    def __init__(self, code: str):
        self.code = str(code or "CONNECTION_STORE_FAILED")
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """One read of the raw connection list and its byte revision.

    Missing ``group_path`` and ``environment`` fields stay missing here. UI
    callers can supply display defaults without persisting them on load.
    """

    items: tuple[dict[str, Any], ...]
    revision: str | None
    format_version: int | None
    needs_upgrade: bool


@dataclass(slots=True)
class _ParsedDocument:
    envelope: dict[str, Any] | None
    items: list[dict[str, Any]]
    format_version: int | None
    needs_upgrade: bool


class _RevisionUnspecified:
    __slots__ = ()


_REVISION_UNSPECIFIED = _RevisionUnspecified()


def _duplicate_key_rejected(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _connection_id(item: Mapping[str, Any]) -> str | None:
    value = item.get("id")
    if isinstance(value, str) and value and len(value) <= MAX_CONNECTION_ID_CHARS:
        return value
    if type(value) is int and len(str(value)) <= MAX_CONNECTION_ID_CHARS:
        return str(value)
    return None


def _validate_json_value(value: Any, depth: int, active: set[int]) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ConnectionStoreError("JSON_TOO_DEEP")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConnectionStoreError("INVALID_RECORD")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            raise ConnectionStoreError("INVALID_RECORD")
        active.add(identity)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_json_value(item, depth + 1, active)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ConnectionStoreError("INVALID_RECORD")
                    _validate_json_value(item, depth + 1, active)
        finally:
            active.remove(identity)
        return
    raise ConnectionStoreError("INVALID_RECORD")


def _copy_json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ConnectionStoreError("INVALID_RECORD")
    try:
        copied = deepcopy(dict(value))
        _validate_json_value(copied, 0, set())
        encoded = json.dumps(
            copied,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise ConnectionStoreError("INVALID_RECORD") from None
    if len(encoded) > MAX_CONNECTION_BYTES:
        raise ConnectionStoreError("CONNECTION_TOO_LARGE")
    return copied


def _validate_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(items, (str, bytes, bytearray)):
        raise ConnectionStoreError("INVALID_DOCUMENT")
    try:
        iterator = iter(items)
        if isinstance(items, Sequence) and len(items) > MAX_CONNECTIONS:
            raise ConnectionStoreError("TOO_MANY_CONNECTIONS")
    except ConnectionStoreError:
        raise
    except Exception:
        raise ConnectionStoreError("INVALID_DOCUMENT") from None
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_bytes = 0
    for index, item in enumerate(iterator):
        if index >= MAX_CONNECTIONS:
            raise ConnectionStoreError("TOO_MANY_CONNECTIONS")
        if not isinstance(item, Mapping):
            raise ConnectionStoreError("INVALID_RECORD")
        copied = _copy_json_mapping(item)
        try:
            item_bytes = len(json.dumps(
                copied,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            raise ConnectionStoreError("INVALID_RECORD") from None
        total_bytes += item_bytes
        if total_bytes > MAX_DOCUMENT_BYTES:
            raise ConnectionStoreError("DOCUMENT_TOO_LARGE")
        item_id = _connection_id(copied)
        if item_id is None:
            raise ConnectionStoreError("INVALID_RECORD")
        if item_id in seen:
            raise ConnectionStoreError("DUPLICATE_ID")
        seen.add(item_id)
        result.append(copied)
    return result


def _revision(raw: bytes | None) -> str | None:
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _parse_document(raw: bytes | None) -> _ParsedDocument:
    if raw is None:
        return _ParsedDocument(None, [], None, False)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ConnectionStoreError("DOCUMENT_TOO_LARGE")
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_key_rejected,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ConnectionStoreError("INVALID_DOCUMENT") from None

    if isinstance(value, list):
        envelope = None
        records = value
        version = None
        needs_upgrade = True
    elif isinstance(value, dict) and isinstance(value.get("items"), list):
        envelope = value
        records = value["items"]
        version_value = value.get("format_version")
        if "format_version" not in value:
            version = None
            needs_upgrade = True
        elif type(version_value) is int and version_value == FORMAT_VERSION:
            version = version_value
            needs_upgrade = False
        elif type(version_value) is int and version_value > FORMAT_VERSION:
            raise ConnectionStoreError("UNSUPPORTED_VERSION")
        else:
            raise ConnectionStoreError("INVALID_VERSION")
    else:
        raise ConnectionStoreError("INVALID_DOCUMENT")

    items = _validate_items(records)
    return _ParsedDocument(envelope, items, version, needs_upgrade)


def _snapshot(parsed: _ParsedDocument, revision: str | None) -> ConnectionSnapshot:
    return ConnectionSnapshot(
        items=tuple(deepcopy(parsed.items)),
        revision=revision,
        format_version=parsed.format_version,
        needs_upgrade=parsed.needs_upgrade,
    )


def _serialize_document(items: list[dict[str, Any]], parsed: _ParsedDocument) -> bytes:
    envelope = deepcopy(parsed.envelope) if parsed.envelope is not None else {}
    envelope["format_version"] = FORMAT_VERSION
    envelope["items"] = deepcopy(items)
    try:
        text = json.dumps(envelope, indent=2, ensure_ascii=False, allow_nan=False)
        raw = (text + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise ConnectionStoreError("INVALID_DOCUMENT") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ConnectionStoreError("DOCUMENT_TOO_LARGE")
    return raw


def _item_index(items: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = _connection_id(item)
        if item_id is None:
            raise ConnectionStoreError("INVALID_RECORD")
        if item_id in index:
            raise ConnectionStoreError("DUPLICATE_ID")
        index[item_id] = dict(item)
    return index


class _OSFileLock:
    """Small bounded advisory lock backed by the platform's file-lock API."""

    def __init__(self, path: Path, timeout: float):
        self.path = path
        self.timeout = timeout
        self.stream = None
        self._module = None

    def __enter__(self) -> _OSFileLock:
        import sys

        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            raise ConnectionStoreError("LOCK_FAILED") from None
        try:
            self.stream = os.fdopen(descriptor, "r+b")
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise ConnectionStoreError("LOCK_FAILED") from None
        try:
            if os.fstat(self.stream.fileno()).st_size == 0:
                self.stream.seek(0, os.SEEK_END)
                self.stream.write(b"\0")
                self.stream.flush()
            if sys.platform == "win32":
                import msvcrt

                self._module = msvcrt
            else:
                import fcntl

                self._module = fcntl
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._try_lock(sys.platform)
                    return self
                except OSError as error:
                    if not self._is_contention(error, sys.platform):
                        raise ConnectionStoreError("LOCK_FAILED") from None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ConnectionStoreError("LOCK_TIMEOUT") from None
                    time.sleep(min(0.01, remaining))
        except ConnectionStoreError:
            try:
                self.stream.close()
            except OSError:
                pass
            self.stream = None
            raise
        except Exception:
            try:
                self.stream.close()
            except OSError:
                pass
            self.stream = None
            raise ConnectionStoreError("LOCK_FAILED") from None

    def _try_lock(self, platform: str) -> None:
        assert self.stream is not None and self._module is not None
        self.stream.seek(0)
        if platform == "win32":
            self._module.locking(self.stream.fileno(), self._module.LK_NBLCK, 1)
        else:
            self._module.flock(self.stream.fileno(), self._module.LOCK_EX | self._module.LOCK_NB)

    @staticmethod
    def _is_contention(error: OSError, platform: str) -> bool:
        if isinstance(error, BlockingIOError):
            return True
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return True
        return platform == "win32" and getattr(error, "winerror", None) in {33, 36}

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if getattr(self._module, "__name__", "") == "msvcrt":
                self._module.locking(self.stream.fileno(), self._module.LK_UNLCK, 1)
            else:
                self._module.flock(self.stream.fileno(), self._module.LOCK_UN)
        except OSError:
            # Closing the handle also releases the kernel lock. Never turn a
            # completed atomic replacement into an apparent failed save.
            pass
        finally:
            try:
                self.stream.close()
            except OSError:
                pass
            self.stream = None


class ConnectionStore:
    """Read and atomically update a caller-selected connections JSON path."""

    def __init__(self, path: str | os.PathLike[str], *, lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS):
        try:
            path_value = os.fspath(path)
        except (TypeError, ValueError, OSError):
            raise ConnectionStoreError("INVALID_PATH") from None
        if not isinstance(path_value, str) or not path_value or "\0" in path_value:
            raise ConnectionStoreError("INVALID_PATH")
        if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)):
            raise ConnectionStoreError("INVALID_LOCK_TIMEOUT")
        timeout = float(lock_timeout)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 10:
            raise ConnectionStoreError("INVALID_LOCK_TIMEOUT")
        try:
            self.path = Path(path_value)
        except (TypeError, ValueError, OSError):
            raise ConnectionStoreError("INVALID_PATH") from None
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_timeout = timeout

    def load(self) -> ConnectionSnapshot:
        """Read without creating directories, locks, backups, or migrations."""

        raw = self._read_raw()
        parsed = _parse_document(raw)
        return _snapshot(parsed, _revision(raw))

    def save(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        expected_revision: str | None,
    ) -> ConnectionSnapshot:
        """Save caller-selected order and deletions with stale-write checking.

        Fields omitted from retained records are merged from the stored row so
        unknown vendor fields and opaque credential tokens survive.
        """

        supplied = _validate_items(items)
        return self._mutate(
            lambda parsed: self._full_list_merge(supplied, parsed.items),
            expected_revision=expected_revision,
        )

    def upsert(
        self,
        patch: Mapping[str, Any],
        *,
        remove_fields: Iterable[str] = (),
        expected_revision: str | None | object = _REVISION_UNSPECIFIED,
    ) -> ConnectionSnapshot:
        """Patch one record by ID without replacing its unprovided fields."""

        if not isinstance(patch, Mapping):
            raise ConnectionStoreError("INVALID_PATCH")
        copied = _copy_json_mapping(patch)
        item_id = _connection_id(copied)
        if item_id is None:
            raise ConnectionStoreError("INVALID_PATCH")
        if isinstance(remove_fields, (str, bytes, bytearray)):
            raise ConnectionStoreError("INVALID_PATCH")
        try:
            removed_values = []
            for index, field in enumerate(remove_fields):
                if index >= MAX_REMOVE_FIELDS:
                    raise ConnectionStoreError("INVALID_PATCH")
                removed_values.append(field)
            removed = tuple(removed_values)
        except Exception:
            raise ConnectionStoreError("INVALID_PATCH") from None
        if any(not isinstance(field, str) or not field or field == "id" for field in removed):
            raise ConnectionStoreError("INVALID_PATCH")
        if len(set(removed)) != len(removed):
            raise ConnectionStoreError("INVALID_PATCH")

        def apply_patch(parsed: _ParsedDocument) -> list[dict[str, Any]]:
            items = deepcopy(parsed.items)
            matches = [index for index, record in enumerate(items) if _connection_id(record) == item_id]
            if len(matches) > 1:
                raise ConnectionStoreError("DUPLICATE_ID")
            if matches:
                index = matches[0]
                merged = items[index]
                prior_id = merged["id"]
                merged.update(copied)
                merged["id"] = prior_id
                for field in removed:
                    merged.pop(field, None)
                items[index] = merged
            else:
                merged = deepcopy(copied)
                for field in removed:
                    merged.pop(field, None)
                items.append(merged)
            return _validate_items(items)

        return self._mutate(apply_patch, expected_revision=expected_revision)

    def delete(
        self,
        connection_id: str,
        *,
        expected_revision: str | None | object = _REVISION_UNSPECIFIED,
    ) -> ConnectionSnapshot:
        """Delete exactly one explicitly named profile; duplicates fail closed."""

        item_id = str(connection_id or "")
        if not item_id:
            raise ConnectionStoreError("INVALID_ID")

        def remove(parsed: _ParsedDocument) -> list[dict[str, Any]]:
            matches = [item for item in parsed.items if _connection_id(item) == item_id]
            if len(matches) > 1:
                raise ConnectionStoreError("DUPLICATE_ID")
            return [deepcopy(item) for item in parsed.items if _connection_id(item) != item_id]

        return self._mutate(remove, expected_revision=expected_revision, no_op_if_unchanged=True)

    def _full_list_merge(
        self,
        supplied: list[dict[str, Any]],
        existing: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        old_by_id = _item_index(existing)
        merged_items: list[dict[str, Any]] = []
        for incoming in supplied:
            item_id = _connection_id(incoming)
            old = old_by_id.get(item_id)
            if old is None:
                merged_items.append(deepcopy(incoming))
                continue
            merged = deepcopy(old)
            merged.update(incoming)
            merged["id"] = old["id"]
            merged_items.append(merged)
        return _validate_items(merged_items)

    def _mutate(
        self,
        operation,
        *,
        expected_revision: str | None | object,
        no_op_if_unchanged: bool = False,
    ) -> ConnectionSnapshot:
        if expected_revision is not _REVISION_UNSPECIFIED:
            if expected_revision is not None and (
                not isinstance(expected_revision, str)
                or len(expected_revision) != 64
                or any(character not in "0123456789abcdef" for character in expected_revision)
            ):
                raise ConnectionStoreError("INVALID_REVISION")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ConnectionStoreError("DIRECTORY_FAILED") from None
        with _OSFileLock(self.lock_path, self.lock_timeout):
            raw = self._read_raw()
            actual_revision = _revision(raw)
            if (
                expected_revision is not _REVISION_UNSPECIFIED
                and expected_revision != actual_revision
            ):
                raise ConnectionStoreError("REVISION_CONFLICT")
            parsed = _parse_document(raw)
            updated_items = operation(parsed)
            payload = _serialize_document(updated_items, parsed)
            if no_op_if_unchanged and updated_items == parsed.items:
                return _snapshot(parsed, actual_revision)
            if raw is not None and parsed.needs_upgrade:
                self._backup_original(raw)
            if payload != raw:
                self._atomic_replace(payload)
            committed = _parse_document(payload)
            return _snapshot(committed, _revision(payload))

    def _read_raw(self) -> bytes | None:
        try:
            with open(self.path, "rb") as stream:
                raw = stream.read(MAX_DOCUMENT_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError:
            raise ConnectionStoreError("READ_FAILED") from None
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise ConnectionStoreError("DOCUMENT_TOO_LARGE")
        return raw

    def _backup_original(self, raw: bytes) -> Path:
        digest = _revision(raw)
        assert digest is not None
        backup_path = self.path.with_name(f"{self.path.name}.pre-v{FORMAT_VERSION}-{digest}.bak")
        try:
            descriptor = os.open(
                backup_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                with open(backup_path, "rb") as stream:
                    saved = stream.read(MAX_DOCUMENT_BYTES + 1)
            except OSError:
                raise ConnectionStoreError("BACKUP_FAILED") from None
            if saved != raw:
                raise ConnectionStoreError("BACKUP_CONFLICT")
            return backup_path
        except OSError:
            raise ConnectionStoreError("BACKUP_FAILED") from None
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            # Keep a partial backup visible for manual recovery; the source
            # file has not been replaced and no existing backup is overwritten.
            raise ConnectionStoreError("BACKUP_FAILED") from None
        return backup_path

    def _atomic_replace(self, payload: bytes) -> None:
        descriptor = -1
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(temporary_path, self.path)
                temporary_path = None
            except OSError:
                # Handle a wrapper/platform error raised after the atomic
                # commit point without claiming that the save was rolled back.
                try:
                    committed = self._read_raw() == payload
                except ConnectionStoreError:
                    raise ConnectionStoreError("COMMIT_UNCERTAIN") from None
                if not committed:
                    raise ConnectionStoreError("WRITE_FAILED") from None
                temporary_path = None
            self._fsync_parent_best_effort()
        except ConnectionStoreError:
            raise
        except OSError:
            raise ConnectionStoreError("WRITE_FAILED") from None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def _fsync_parent_best_effort(self) -> None:
        if os.name == "nt":
            return
        descriptor = -1
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            # os.replace has committed. A directory-fsync error cannot be
            # reported as a failed save because the prior bytes are gone.
            pass
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
