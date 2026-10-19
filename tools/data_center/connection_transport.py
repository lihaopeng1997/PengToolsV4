"""Typed Data Center SSH/TLS connection settings and leased connections.

Transport profile values are read from the selected connection row only.  This
module has no Qt or driver imports; secrets remain opaque until the connection
owner resolves them immediately before authentication.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import threading
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from .readonly_driver_adapters import canonical_provider


class ConnectionTransportError(RuntimeError):
    """A stable, non-secret connection transport error."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "TRANSPORT_FAILED")
        super().__init__(self.code)


_TRANSPORT_PROVIDERS = frozenset({"mysql", "oceanbase_mysql", "oracle", "oceanbase_oracle", "redis", "mongodb"})


def canonical_transport_provider(provider: Any, mode: Any = None) -> str:
    raw = str(provider or "").strip().casefold()
    if raw in {"redis", "mongodb", "mongo"}:
        return "mongodb" if raw == "mongo" else raw
    return canonical_provider(provider, mode)


def mongo_transport_uri(profile: Mapping[str, Any]) -> str:
    values = [str(profile.get(key) or "").strip() for key in
              ("uri", "connection_uri", "connection_string") if profile.get(key)]
    host = str(profile.get("host") or "").strip()
    if host.casefold().startswith(("mongodb://", "mongodb+srv://")):
        values.append(host)
    if len(set(values)) > 1:
        raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
    return values[0] if values else ""


def _nosql_target(profile: Mapping[str, Any], provider: str) -> tuple[str, int]:
    default = 6379 if provider == "redis" else 27017
    if any(profile.get(key) for key in ("replica_set", "replicaSet", "replica_set_name")):
        raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")
    uri = mongo_transport_uri(profile) if provider == "mongodb" else ""
    if uri:
        try:
            parsed = urlsplit(uri)
            if parsed.scheme.casefold() != "mongodb" or "," in parsed.netloc or parsed.fragment:
                raise ValueError()
            host, port = parsed.hostname, parsed.port or default
            if not host or not 1 <= port <= 65535:
                raise ValueError()
            for key, value in parse_qsl(parsed.query):
                key = key.casefold()
                if key in {"replicaset", "loadbalanced", "tls", "ssl"} or key.startswith("tls"):
                    raise ValueError()
                if key == "directconnection" and value.casefold() != "true":
                    raise ValueError()
        except (ValueError, TypeError):
            raise ConnectionTransportError("UNSUPPORTED_TRANSPORT") from None
    else:
        host, port = _text(profile, "host"), _port(profile, "port", default)
    seeds = profile.get("seed_nodes")
    if seeds:
        if not isinstance(seeds, (list, tuple)) or len(seeds) != 1 or not isinstance(seeds[0], Mapping):
            raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")
        seed_host = _text(seeds[0], "host")
        seed_port = _port(seeds[0], "port", default)
        if uri and (seed_host, seed_port) != (host, port):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        host, port = seed_host, seed_port
    return host, port
_SSH_FIELDS = (
    "ssh_host", "ssh_username", "ssh_auth_method",
    "ssh_password_token", "ssh_private_key_path", "ssh_key_passphrase_token",
)
_TLS_FIELDS = (
    "tls_ca_path", "tls_client_cert_path", "tls_client_key_path",
    "tls_client_key_password_token",
)
_LEGACY_OR_UNSAFE_FIELDS = frozenset({
    "ssh", "ssh_tunnel", "ssh_user", "ssh_password", "ssh_key", "ssh_key_path",
    "ssl", "tls", "ssl_ca", "ssl_cert", "ssl_key", "ssl_key_password",
    "ssl_verify_cert", "ssl_verify_identity", "ssl_disabled",
    "tls_verify_cert", "tls_verify_identity", "tls_disabled",
    "wallet_location", "wallet_password", "ssl_server_dn_match",
    "ssl_server_cert_dn", "ssl_path", "ssl_pwd",
})


def _strict_bool(profile: Mapping[str, Any], key: str) -> bool:
    value = profile.get(key, False)
    if type(value) is not bool:
        raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
    return value


def _text(profile: Mapping[str, Any], key: str) -> str:
    value = profile.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
    value = value.strip()
    if "\x00" in value:
        raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
    return value


def _port(profile: Mapping[str, Any], key: str, default: int) -> int:
    value = profile.get(key, default)
    if type(value) is not int or not 1 <= value <= 65535:
        raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
    return value


def _has_values(profile: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
    return any(profile.get(key) not in (None, "", False, 0) for key in fields)


def _encrypted_token(value: str) -> bool:
    return not value or value.startswith(("dpapi:", "enc:"))


@dataclass(frozen=True, slots=True)
class DataCenterConnectionTransport:
    """Validated transport projection for one supported single target."""

    provider: str
    database_host: str
    database_port: int
    ssh_enabled: bool
    ssh_host: str
    ssh_port: int
    ssh_username: str
    ssh_auth_method: str
    ssh_password_token: str
    ssh_private_key_path: str
    ssh_key_passphrase_token: str
    tls_enabled: bool
    tls_ca_path: str
    tls_client_cert_path: str
    tls_client_key_path: str
    tls_client_key_password_token: str

    @classmethod
    def from_profile(
        cls,
        profile: Mapping[str, Any],
        provider: Any,
        mode: Any = None,
    ) -> "DataCenterConnectionTransport | None":
        if not isinstance(profile, Mapping):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        try:
            selected = canonical_transport_provider(provider, mode)
        except Exception:
            raise ConnectionTransportError("UNSUPPORTED_TRANSPORT") from None

        # Existing generic/legacy transport fields stay fail-closed.  In
        # particular, never interpret an old key/URI option as this profile.
        for key in _LEGACY_OR_UNSAFE_FIELDS:
            if key in profile and profile.get(key) not in (None, "", False, 0, {}, [], ()):
                raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")

        ssh_enabled = _strict_bool(profile, "ssh_enabled")
        tls_enabled = _strict_bool(profile, "tls_enabled")
        ssh_values = _has_values(profile, _SSH_FIELDS) or (
            "ssh_port" in profile and profile.get("ssh_port") not in (None, "", 22)
        )
        tls_values = _has_values(profile, _TLS_FIELDS)
        if not ssh_enabled and ssh_values:
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        if not tls_enabled and tls_values:
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        if not ssh_enabled and not tls_enabled:
            return None
        if selected not in _TRANSPORT_PROVIDERS:
            raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")

        mode_value = profile.get("mode") or mode or ""
        if not isinstance(mode_value, str):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        mode_text = mode_value.strip().casefold()
        allowed_modes = {"", "standalone", "single"}
        if selected in {"mysql", "oceanbase_mysql"}:
            allowed_modes.add("mysql")
        if selected in {"oracle", "oceanbase_oracle"}:
            allowed_modes.add("oracle")
            if tls_enabled:
                raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")
        if mode_text not in allowed_modes:
            raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")
        if selected in {"redis", "mongodb"}:
            if ssh_enabled and tls_enabled:
                # These public SDKs cannot separate the dial endpoint from
                # the TLS server identity. Never disable hostname verification.
                raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")
            database_host, database_port = _nosql_target(profile, selected)
            if selected == "mongodb" and tls_enabled and any(profile.get(key) for key in _TLS_FIELDS[1:]):
                raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")
        else:
            database_host = _text(profile, "host")
            default_port = {"mysql": 3306, "oracle": 1521}.get(selected, 2883)
            database_port = _port(profile, "port", default_port)
        if not database_host or any(value in database_host for value in ("://", ",", "/", "\x00")):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")

        ssh_host = _text(profile, "ssh_host")
        ssh_username = _text(profile, "ssh_username")
        auth_method = _text(profile, "ssh_auth_method").casefold()
        ssh_password_token = _text(profile, "ssh_password_token")
        private_key_path = _text(profile, "ssh_private_key_path")
        key_passphrase_token = _text(profile, "ssh_key_passphrase_token")
        if ssh_enabled:
            if not ssh_host or "://" in ssh_host or not ssh_username:
                raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
            if auth_method == "password":
                if not ssh_password_token or not _encrypted_token(ssh_password_token) or private_key_path or key_passphrase_token:
                    raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
            elif auth_method == "private_key":
                if not private_key_path or ssh_password_token or not _encrypted_token(key_passphrase_token):
                    raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
            else:
                raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        elif any((ssh_host, ssh_username, auth_method, ssh_password_token, private_key_path, key_passphrase_token)):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")

        ca_path = _text(profile, "tls_ca_path")
        client_cert_path = _text(profile, "tls_client_cert_path")
        client_key_path = _text(profile, "tls_client_key_path")
        client_key_token = _text(profile, "tls_client_key_password_token")
        if not _encrypted_token(client_key_token):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        if tls_enabled and bool(client_cert_path) != bool(client_key_path):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")
        if not tls_enabled and any((ca_path, client_cert_path, client_key_path, client_key_token)):
            raise ConnectionTransportError("TRANSPORT_PROFILE_INVALID")

        return cls(
            provider=selected,
            database_host=database_host,
            database_port=database_port,
            ssh_enabled=ssh_enabled,
            ssh_host=ssh_host,
            ssh_port=_port(profile, "ssh_port", 22) if ssh_enabled else 22,
            ssh_username=ssh_username,
            ssh_auth_method=auth_method,
            ssh_password_token=ssh_password_token,
            ssh_private_key_path=private_key_path,
            ssh_key_passphrase_token=key_passphrase_token,
            tls_enabled=tls_enabled,
            tls_ca_path=ca_path,
            tls_client_cert_path=client_cert_path,
            tls_client_key_path=client_key_path,
            tls_client_key_password_token=client_key_token,
        )

    def binding_key(self) -> tuple[Any, ...]:
        """A non-secret, immutable component for an owner/tunnel cache key."""
        return (
            self.provider, self.database_host, self.database_port,
            self.ssh_enabled, self.ssh_host, self.ssh_port, self.ssh_username,
            self.ssh_auth_method, self.ssh_private_key_path,
            self.tls_enabled, self.tls_ca_path, self.tls_client_cert_path,
            self.tls_client_key_path,
        )


def resolve_secret_token(token: str) -> str:
    """Resolve one opaque secure_store token without retaining vendor errors."""
    if not token:
        return ""
    try:
        from tools.secure_store import decrypt_secret

        value = decrypt_secret(token)
    except Exception:
        raise ConnectionTransportError("TRANSPORT_SECRET_UNAVAILABLE") from None
    if value is None or str(value) == "":
        raise ConnectionTransportError("TRANSPORT_SECRET_UNAVAILABLE")
    return str(value)


class LeasedConnection:
    """DB-API proxy that releases a tunnel only after DB close succeeds.

    If tunnel threads still need cleanup, the released lease handle is retained
    so a subsequent owner-thread close can retry the real drain without
    replaying the underlying database close.
    """

    def __init__(self, connection: Any, lease: Any) -> None:
        self._connection = connection
        self._lease = lease
        self._database_closed = False
        self._complete = False
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            if self._complete:
                return
            if not self._database_closed:
                self._connection.close()
                self._database_closed = True
            try:
                complete = bool(self._lease.release())
            except Exception:
                complete = False
            if not complete:
                raise ConnectionTransportError("SSH_CLEANUP_PENDING")
            self._complete = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __getitem__(self, key: Any) -> Any:
        return self._connection[key]

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_connection", "_lease", "_database_closed", "_complete", "_lock"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._connection, name, value)


_default_manager: Any = None
_default_manager_lock = threading.RLock()
_pending_connections: dict[int, Any] = {}


def retain_pending_connection(connection: Any) -> None:
    """Retain a failed close owner so shutdown can retry actual cleanup."""
    with _default_manager_lock:
        _pending_connections[id(connection)] = connection


def default_ssh_tunnel_manager() -> Any:
    """Lazily create the one Data Center manager; importing remains offline."""
    global _default_manager
    with _default_manager_lock:
        if _default_manager is None:
            from config import DATA_CENTER_SSH_KNOWN_HOSTS_FILE
            from tools.data_center.ssh_tunnel import DataCenterSshTunnelManager

            _default_manager = DataCenterSshTunnelManager(DATA_CENTER_SSH_KNOWN_HOSTS_FILE)
        return _default_manager


def retry_default_transport_cleanup() -> bool:
    """Retry released tunnel cleanup without interrupting active DB owners."""
    with _default_manager_lock:
        manager = _default_manager
        pending = tuple(_pending_connections.items())
    complete = True
    for key, connection in pending:
        try:
            connection.close()
        except Exception:
            complete = False
        else:
            with _default_manager_lock:
                _pending_connections.pop(key, None)
    drained = True if manager is None else bool(manager.drain_released())
    return complete and drained


__all__ = [
    "ConnectionTransportError",
    "canonical_transport_provider",
    "mongo_transport_uri",
    "DataCenterConnectionTransport",
    "LeasedConnection",
    "default_ssh_tunnel_manager",
    "resolve_secret_token",
    "retry_default_transport_cleanup",
]
