"""Data Center-owned, host-key-pinned single-target SSH forwarding."""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
import hashlib
import json
import os
import select
import socket
import tempfile
import threading
import time
import uuid
from typing import Any, Callable

from .connection_transport import ConnectionTransportError, DataCenterConnectionTransport, resolve_secret_token


class SshTunnelError(RuntimeError):
    """Stable SSH error without server, credential, or library details."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "SSH_FAILED")
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class HostKeyInspection:
    state: str
    host: str
    port: int
    key_type: str
    fingerprint: str
    challenge_id: str = ""


class DataCenterHostKeyStore:
    """Atomic, DC-specific pin store keyed by normalized SSH host and port."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)
        if not self.path:
            raise SshTunnelError("SSH_TRUST_STORE_INVALID")
        self._lock = threading.RLock()

    @staticmethod
    def _host(host: str) -> str:
        try:
            value = str(host or "").strip().rstrip(".").encode("idna").decode("ascii").casefold()
        except (UnicodeError, ValueError):
            raise SshTunnelError("SSH_HOST_INVALID") from None
        if not value or "\x00" in value:
            raise SshTunnelError("SSH_HOST_INVALID")
        return value

    def _read(self) -> dict[tuple[str, int], tuple[str, str]]:
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                document = json.load(stream)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError):
            raise SshTunnelError("SSH_TRUST_STORE_UNAVAILABLE") from None
        if not isinstance(document, dict) or document.get("version") != 1:
            raise SshTunnelError("SSH_TRUST_STORE_INVALID")
        records = document.get("hosts")
        if not isinstance(records, list):
            raise SshTunnelError("SSH_TRUST_STORE_INVALID")
        result: dict[tuple[str, int], tuple[str, str]] = {}
        for item in records:
            if not isinstance(item, dict):
                raise SshTunnelError("SSH_TRUST_STORE_INVALID")
            host = item.get("host")
            port = item.get("port")
            key_type = item.get("key_type")
            fingerprint = item.get("fingerprint")
            if (
                not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535
                or not isinstance(key_type, str) or not key_type
                or not isinstance(fingerprint, str) or not fingerprint.startswith("SHA256:")
            ):
                raise SshTunnelError("SSH_TRUST_STORE_INVALID")
            key = (self._host(host), port)
            if key in result:
                raise SshTunnelError("SSH_TRUST_STORE_INVALID")
            result[key] = (key_type, fingerprint)
        return result

    def lookup(self, host: str, port: int) -> tuple[str, str] | None:
        with self._lock:
            return self._read().get((self._host(host), port))

    def add_first_use(self, host: str, port: int, key_type: str, fingerprint: str) -> None:
        normalized = self._host(host)
        if type(port) is not int or not 1 <= port <= 65535 or not key_type or not fingerprint.startswith("SHA256:"):
            raise SshTunnelError("SSH_TRUST_STORE_INVALID")
        with self._lock:
            entries = self._read()
            prior = entries.get((normalized, port))
            if prior is not None:
                if prior == (key_type, fingerprint):
                    return
                raise SshTunnelError("SSH_HOST_KEY_CHANGED")
            entries[(normalized, port)] = (key_type, fingerprint)
            document = {
                "version": 1,
                "hosts": [
                    {"host": host_key, "port": port_key, "key_type": value[0], "fingerprint": value[1]}
                    for (host_key, port_key), value in sorted(entries.items())
                ],
            }
            parent = os.path.dirname(os.path.abspath(self.path))
            try:
                os.makedirs(parent, exist_ok=True)
                fd, temp_path = tempfile.mkstemp(prefix=".dc-hostkeys-", suffix=".tmp", dir=parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                        json.dump(document, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp_path, self.path)
                finally:
                    try:
                        os.unlink(temp_path)
                    except FileNotFoundError:
                        pass
            except OSError:
                raise SshTunnelError("SSH_TRUST_STORE_UNAVAILABLE") from None


@dataclass(slots=True)
class _TunnelEntry:
    cache_key: tuple[Any, ...]
    transport: Any
    listener: socket.socket
    dial_host: str
    dial_port: int
    target_host: str
    target_port: int
    references: int = 0
    stopping: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    locals: set[socket.socket] = field(default_factory=set)
    channels: set[Any] = field(default_factory=set)
    bridges: set[threading.Thread] = field(default_factory=set)
    accept_thread: threading.Thread | None = None
    cleanup_pending: bool = False


class SshTunnelLease:
    """An idempotent reference held by one DB/session owner."""

    def __init__(self, manager: "DataCenterSshTunnelManager", cache_key: tuple[Any, ...], host: str, port: int):
        self._manager = manager
        self._cache_key = cache_key
        self.dial_host = host
        self.dial_port = port
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> bool:
        with self._lock:
            first = not self._released
            self._released = True
        return self._manager._release(self._cache_key, decrement=first)


class DataCenterSshTunnelManager:
    """Real Paramiko single-target forwarder with reference-counted cleanup."""

    def __init__(
        self,
        trust_store_path: str | os.PathLike[str],
        *,
        connect_timeout: float = 8.0,
        cleanup_timeout: float = 2.0,
        secret_resolver: Callable[[str], str] = resolve_secret_token,
        paramiko_module: Any = None,
        socket_module: Any = socket,
    ) -> None:
        self.trust_store = DataCenterHostKeyStore(trust_store_path)
        self.connect_timeout = max(1.0, min(float(connect_timeout), 30.0))
        self.cleanup_timeout = max(0.1, min(float(cleanup_timeout), 10.0))
        self.secret_resolver = secret_resolver
        self._paramiko_module = paramiko_module
        self._socket_module = socket_module
        self._lock = threading.RLock()
        self._entries: dict[tuple[Any, ...], _TunnelEntry] = {}
        self._pending_transports: list[tuple[Any, Any]] = []
        self._challenges: dict[str, tuple[tuple[Any, ...], str, int, str, str]] = {}
        self._closed = False

    def _paramiko(self) -> Any:
        if self._paramiko_module is None:
            try:
                import paramiko
            except ImportError:
                raise SshTunnelError("SSH_DRIVER_UNAVAILABLE") from None
            self._paramiko_module = paramiko
        return self._paramiko_module

    def _open_handshake(self, spec: DataCenterConnectionTransport) -> tuple[Any, Any, str, str]:
        raw_socket = None
        transport = None
        try:
            raw_socket = self._socket_module.create_connection(
                (spec.ssh_host, spec.ssh_port), timeout=self.connect_timeout
            )
            transport = self._paramiko().Transport(raw_socket)
            transport.start_client(timeout=self.connect_timeout)
            key = transport.get_remote_server_key()
            key_type = str(key.get_name())
            fingerprint_bytes = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
            return raw_socket, transport, key_type, "SHA256:" + fingerprint_bytes
        except SshTunnelError:
            self._close_unowned_transport(transport, raw_socket)
            raise
        except Exception:
            self._close_unowned_transport(transport, raw_socket)
            raise SshTunnelError("SSH_HANDSHAKE_FAILED") from None

    def _close_unowned_transport(self, transport: Any, raw_socket: Any) -> bool:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
            join = getattr(transport, "join", None)
            if callable(join):
                try:
                    join(self.cleanup_timeout)
                except Exception:
                    pass
            alive = getattr(transport, "is_alive", None)
            try:
                if callable(alive) and alive():
                    self._remember_pending_transport(transport, raw_socket)
                    return False
            except Exception:
                self._remember_pending_transport(transport, raw_socket)
                return False
        if raw_socket is not None:
            try:
                raw_socket.close()
            except Exception:
                pass
        self._forget_pending_transport(transport)
        return True

    def _remember_pending_transport(self, transport: Any, raw_socket: Any) -> None:
        if transport is None:
            return
        with self._lock:
            if not any(current is transport for current, _ in self._pending_transports):
                self._pending_transports.append((transport, raw_socket))

    def _forget_pending_transport(self, transport: Any) -> None:
        if transport is None:
            return
        with self._lock:
            self._pending_transports = [
                item for item in self._pending_transports if item[0] is not transport
            ]

    def inspect_host_key(self, spec: DataCenterConnectionTransport) -> HostKeyInspection:
        if not spec.ssh_enabled:
            raise SshTunnelError("SSH_NOT_ENABLED")
        raw_socket, transport, key_type, fingerprint = self._open_handshake(spec)
        try:
            trusted = self.trust_store.lookup(spec.ssh_host, spec.ssh_port)
            if trusted is None:
                state = "unknown"
            elif trusted == (key_type, fingerprint):
                state = "trusted"
            else:
                state = "changed"
        finally:
            if not self._close_unowned_transport(transport, raw_socket):
                raise SshTunnelError("SSH_CLEANUP_PENDING")
        challenge_id = ""
        if state == "unknown":
            challenge_id = uuid.uuid4().hex
            with self._lock:
                self._challenges[challenge_id] = (
                    spec.binding_key(), spec.ssh_host, spec.ssh_port, key_type, fingerprint
                )
        return HostKeyInspection(state, spec.ssh_host, spec.ssh_port, key_type, fingerprint, challenge_id)

    def confirm_first_use(
        self,
        spec: DataCenterConnectionTransport,
        challenge_id: str,
        accepted_fingerprint: str,
    ) -> HostKeyInspection:
        with self._lock:
            challenge = self._challenges.get(str(challenge_id or ""))
        if challenge is None or challenge[0] != spec.binding_key():
            raise SshTunnelError("SSH_CHALLENGE_INVALID")
        raw_socket, transport, key_type, fingerprint = self._open_handshake(spec)
        try:
            if (spec.ssh_host, spec.ssh_port, key_type, fingerprint) != challenge[1:]:
                raise SshTunnelError("SSH_HOST_KEY_CHANGED")
            if accepted_fingerprint != fingerprint:
                raise SshTunnelError("SSH_CONFIRMATION_MISMATCH")
            trusted = self.trust_store.lookup(spec.ssh_host, spec.ssh_port)
            if trusted is not None and trusted != (key_type, fingerprint):
                raise SshTunnelError("SSH_HOST_KEY_CHANGED")
            self.trust_store.add_first_use(spec.ssh_host, spec.ssh_port, key_type, fingerprint)
        finally:
            if not self._close_unowned_transport(transport, raw_socket):
                raise SshTunnelError("SSH_CLEANUP_PENDING")
        with self._lock:
            self._challenges.pop(challenge_id, None)
        return HostKeyInspection("trusted", spec.ssh_host, spec.ssh_port, key_type, fingerprint)

    def acquire(
        self,
        spec: DataCenterConnectionTransport,
        *,
        connection_id: str,
        profile_revision: str,
    ) -> SshTunnelLease:
        if not spec.ssh_enabled or not str(connection_id or "") or not str(profile_revision or ""):
            raise SshTunnelError("SSH_PROFILE_INVALID")
        with self._lock:
            if self._closed:
                raise SshTunnelError("SSH_MANAGER_CLOSED")
        expected = self.trust_store.lookup(spec.ssh_host, spec.ssh_port)
        if expected is None:
            raise SshTunnelError("SSH_HOST_KEY_CONFIRM_REQUIRED")
        raw_socket, transport, key_type, fingerprint = self._open_handshake(spec)
        if expected != (key_type, fingerprint):
            self._close_unowned_transport(transport, raw_socket)
            raise SshTunnelError("SSH_HOST_KEY_CHANGED")
        cache_key = (
            str(connection_id), str(profile_revision), spec.provider,
            spec.database_host, spec.database_port, spec.ssh_host, spec.ssh_port,
            spec.ssh_username, spec.ssh_auth_method, spec.ssh_private_key_path,
            fingerprint,
        )
        with self._lock:
            if self._closed:
                self._close_unowned_transport(transport, raw_socket)
                raise SshTunnelError("SSH_MANAGER_CLOSED")
            existing = self._entries.get(cache_key)
            if existing is not None and not existing.cleanup_pending and not existing.stopping.is_set():
                if not self._close_unowned_transport(transport, raw_socket):
                    raise SshTunnelError("SSH_CLEANUP_PENDING")
                existing.references += 1
                return SshTunnelLease(self, cache_key, existing.dial_host, existing.dial_port)
            if existing is not None:
                self._close_unowned_transport(transport, raw_socket)
                raise SshTunnelError("SSH_CLEANUP_PENDING")
            listener = None
            try:
                self._authenticate(transport, spec)
                listener = self._socket_module.socket(
                    self._socket_module.AF_INET, self._socket_module.SOCK_STREAM
                )
                listener.setsockopt(self._socket_module.SOL_SOCKET, self._socket_module.SO_REUSEADDR, 0)
                listener.bind(("127.0.0.1", 0))
                listener.listen(16)
                listener.settimeout(0.2)
                dial_host, dial_port = listener.getsockname()
                if dial_host != "127.0.0.1" or type(dial_port) is not int or dial_port <= 0:
                    raise SshTunnelError("SSH_FORWARD_UNAVAILABLE")
                entry = _TunnelEntry(
                    cache_key, transport, listener, dial_host, dial_port,
                    spec.database_host, spec.database_port, references=1,
                )
                accept_thread = threading.Thread(
                    target=self._accept_loop, args=(entry,), name="dc-ssh-forward-accept", daemon=True
                )
                entry.accept_thread = accept_thread
                self._entries[cache_key] = entry
                accept_thread.start()
                return SshTunnelLease(self, cache_key, dial_host, dial_port)
            except Exception as error:
                if listener is not None:
                    try:
                        listener.close()
                    except Exception:
                        pass
                self._close_unowned_transport(transport, raw_socket)
                if isinstance(error, SshTunnelError):
                    raise
                raise SshTunnelError("SSH_FORWARD_UNAVAILABLE") from None

    def _authenticate(self, transport: Any, spec: DataCenterConnectionTransport) -> None:
        try:
            if spec.ssh_auth_method == "password":
                secret = self.secret_resolver(spec.ssh_password_token)
                transport.auth_password(spec.ssh_username, secret)
            else:
                passphrase = self.secret_resolver(spec.ssh_key_passphrase_token) if spec.ssh_key_passphrase_token else None
                key = self._paramiko().PKey.from_path(spec.ssh_private_key_path, passphrase=passphrase)
                transport.auth_publickey(spec.ssh_username, key)
            if not bool(transport.is_authenticated()):
                raise SshTunnelError("SSH_AUTHENTICATION_FAILED")
        except SshTunnelError:
            raise
        except ConnectionTransportError:
            raise SshTunnelError("SSH_SECRET_UNAVAILABLE") from None
        except Exception:
            raise SshTunnelError("SSH_AUTHENTICATION_FAILED") from None

    def _accept_loop(self, entry: _TunnelEntry) -> None:
        while not entry.stopping.is_set():
            try:
                local, origin = entry.listener.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            if entry.stopping.is_set():
                try:
                    local.close()
                except Exception:
                    pass
                return
            try:
                channel = entry.transport.open_channel(
                    "direct-tcpip",
                    (entry.target_host, entry.target_port),
                    origin,
                    timeout=self.connect_timeout,
                )
                if channel is None:
                    raise SshTunnelError("SSH_FORWARD_UNAVAILABLE")
                with entry.lock:
                    if entry.stopping.is_set():
                        local.close()
                        channel.close()
                        return
                    entry.locals.add(local)
                    entry.channels.add(channel)
                    bridge = threading.Thread(
                        target=self._bridge, args=(entry, local, channel),
                        name="dc-ssh-forward-bridge", daemon=True,
                    )
                    entry.bridges.add(bridge)
                    bridge.start()
            except Exception:
                try:
                    local.close()
                except Exception:
                    pass

    @staticmethod
    def _bridge(entry: _TunnelEntry, local: socket.socket, channel: Any) -> None:
        try:
            while not entry.stopping.is_set():
                readable, _, _ = select.select([local, channel], [], [], 0.2)
                for source in readable:
                    payload = source.recv(65536)
                    if not payload:
                        return
                    target = channel if source is local else local
                    target.sendall(payload)
        except (OSError, EOFError, ValueError):
            return
        finally:
            try:
                local.close()
            except Exception:
                pass
            try:
                channel.close()
            except Exception:
                pass
            with entry.lock:
                entry.locals.discard(local)
                entry.channels.discard(channel)
                entry.bridges.discard(threading.current_thread())

    def _release(self, cache_key: tuple[Any, ...], *, decrement: bool) -> bool:
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry is None:
                return True
            if decrement:
                if entry.references <= 0:
                    return False
                entry.references -= 1
                if entry.references > 0:
                    return True
        return self._close_entry(entry)

    def _close_entry(self, entry: _TunnelEntry) -> bool:
        with self._lock:
            if entry.references > 0:
                return True
            entry.cleanup_pending = True
        entry.stopping.set()
        try:
            entry.listener.close()
        except Exception:
            pass
        with entry.lock:
            locals_copy = tuple(entry.locals)
            channels_copy = tuple(entry.channels)
        for local in locals_copy:
            try:
                local.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                local.close()
            except Exception:
                pass
        for channel in channels_copy:
            try:
                channel.close()
            except Exception:
                pass
        try:
            entry.transport.close()
        except Exception:
            pass

        deadline = time.monotonic() + self.cleanup_timeout
        threads = [entry.accept_thread, *tuple(entry.bridges), entry.transport]
        for worker in threads:
            if worker is None or worker is threading.current_thread():
                continue
            join = getattr(worker, "join", None)
            alive = getattr(worker, "is_alive", None)
            if callable(join):
                try:
                    join(max(0.0, deadline - time.monotonic()))
                except Exception:
                    pass
            try:
                if callable(alive) and alive():
                    return False
            except Exception:
                return False
        with self._lock:
            if entry.references > 0:
                entry.cleanup_pending = False
                return True
            if self._entries.get(entry.cache_key) is entry:
                self._entries.pop(entry.cache_key, None)
        return True

    def _drain_pending_transports(self) -> bool:
        with self._lock:
            pending = tuple(self._pending_transports)
        complete = True
        for transport, raw_socket in pending:
            complete = self._close_unowned_transport(transport, raw_socket) and complete
        return complete and not self._pending_transports

    def drain_released(self) -> bool:
        with self._lock:
            entries = tuple(entry for entry in self._entries.values() if entry.references == 0)
        complete = True
        for entry in entries:
            complete = self._close_entry(entry) and complete
        pending_complete = self._drain_pending_transports()
        with self._lock:
            active = any(entry.references > 0 for entry in self._entries.values())
            pending_entries = any(entry.cleanup_pending and entry.references == 0 for entry in self._entries.values())
        return complete and pending_complete and not pending_entries and not active

    def close(self) -> bool:
        with self._lock:
            self._closed = True
        return self.drain_released()


__all__ = [
    "DataCenterHostKeyStore",
    "DataCenterSshTunnelManager",
    "HostKeyInspection",
    "SshTunnelError",
    "SshTunnelLease",
]
