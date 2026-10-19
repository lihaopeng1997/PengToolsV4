"""Data Center-only single-target NoSQL transport and native opener."""
from __future__ import annotations

import ssl
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .connection_transport import (
    ConnectionTransportError, DataCenterConnectionTransport, LeasedConnection,
    canonical_transport_provider, default_ssh_tunnel_manager, mongo_transport_uri,
    resolve_secret_token,
    retain_pending_connection,
)


def connect_nosql(provider: Any, profile: Mapping[str, Any], secret: Any, /, **kwargs: Any) -> Any:
    provider = canonical_transport_provider(provider, profile.get("mode"))
    spec = DataCenterConnectionTransport.from_profile(profile, provider, profile.get("mode"))
    if provider not in {"redis", "mongodb"} or spec is None:
        raise ConnectionTransportError("UNSUPPORTED_TRANSPORT")
    options = dict(kwargs.get("options") or kwargs.get("client_options") or {})
    lease = None
    client = None
    host, port = spec.database_host, spec.database_port
    try:
        if spec.ssh_enabled:
            from .host_nosql import compute_profile_revision

            manager = kwargs.get("ssh_manager") or default_ssh_tunnel_manager()
            lease = manager.acquire(
                spec,
                connection_id=str(kwargs.get("connection_id") or profile.get("id") or profile.get("connection_id") or ""),
                profile_revision=str(kwargs.get("profile_revision") or compute_profile_revision(profile)),
            )
            host, port = lease.dial_host, lease.dial_port
        if provider == "redis":
            import redis
            from tools.db_connect import redis_auth_kwargs

            args = dict(
                host=host, port=port, db=int(options.get("database", profile.get("database") or 0)),
                decode_responses=False,
                socket_connect_timeout=float(options.get("socket_connect_timeout") or kwargs.get("connect_timeout") or 8),
                socket_timeout=float(options.get("socket_timeout") or kwargs.get("read_timeout") or 8),
                **redis_auth_kwargs(profile, str(secret or "")),
            )
            if spec.tls_enabled:
                args.update(ssl=True, ssl_cert_reqs=ssl.CERT_REQUIRED, ssl_check_hostname=True)
                if spec.tls_ca_path:
                    args["ssl_ca_certs"] = spec.tls_ca_path
                if spec.tls_client_cert_path:
                    args.update(ssl_certfile=spec.tls_client_cert_path, ssl_keyfile=spec.tls_client_key_path)
                    if spec.tls_client_key_password_token:
                        args["ssl_password"] = resolve_secret_token(spec.tls_client_key_password_token)
            client = redis.Redis(**args)
            if kwargs.get("probe"):
                client.ping()
        else:
            from pymongo import MongoClient

            args = dict(
                serverSelectionTimeoutMS=int(options.get("serverSelectionTimeoutMS") or float(kwargs.get("connect_timeout") or 8) * 1000),
                socketTimeoutMS=int(options.get("socketTimeoutMS") or float(kwargs.get("read_timeout") or 8) * 1000),
                directConnection=True,
            )
            username = str(profile.get("username") or "").strip()
            if username:
                args.update(username=username, password=str(secret or ""),
                            authSource=options.get("auth_source") or profile.get("auth_source") or profile.get("database") or "admin")
                mechanism = options.get("auth_mechanism") or profile.get("auth_mechanism")
                if mechanism and str(mechanism).casefold() != "auto":
                    args["authMechanism"] = mechanism
            if spec.tls_enabled:
                args.update(tls=True, tlsAllowInvalidCertificates=False, tlsAllowInvalidHostnames=False)
                if spec.tls_ca_path:
                    args["tlsCAFile"] = spec.tls_ca_path
            uri = mongo_transport_uri(profile)
            if uri:
                parsed = urlsplit(uri)
                userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
                dial_host = f"[{host}]" if ":" in host else host
                rewritten = urlunsplit((parsed.scheme, f"{userinfo}{dial_host}:{port}", parsed.path, parsed.query, ""))
                client = MongoClient(rewritten, **args)
            else:
                client = MongoClient(host=host, port=port, **args)
            if kwargs.get("probe"):
                client[str(profile.get("database") or "admin")].command("ping")
        return LeasedConnection(client, lease) if lease is not None else client
    except Exception as error:
        if client is not None:
            owner = LeasedConnection(client, lease) if lease is not None else client
            try:
                owner.close()
                lease = None
            except Exception:
                retain_pending_connection(owner)
                raise ConnectionTransportError("SSH_CLEANUP_PENDING") from None
        if lease is not None:
            try:
                complete = bool(lease.release())
            except Exception:
                complete = False
            if not complete:
                raise ConnectionTransportError("SSH_CLEANUP_PENDING") from None
        raise ConnectionTransportError(getattr(error, "code", "") or "CONNECT_FAILED") from None


class _OwnedMongoDatabase:
    def __init__(self, client: Any, name: str) -> None:
        self.client = client
        self._database = client[name]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)

    def __getitem__(self, name: str) -> Any:
        return self._database[name]

    def close(self) -> None:
        self.client.close()


def open_data_center_nosql_connection(profile: Mapping[str, Any]) -> Any:
    """Native workbench seam; ordinary Redis/Mongo panels keep their opener."""
    from tools.db_connect import _decrypt, open_connection

    provider = profile.get("provider") or profile.get("dialect")
    spec = DataCenterConnectionTransport.from_profile(profile, provider, profile.get("mode"))
    if spec is None:
        return open_connection(profile)
    client = connect_nosql(provider, profile, _decrypt(profile.get("password") or ""), probe=True)
    if spec.provider == "mongodb":
        return _OwnedMongoDatabase(client, str(profile.get("database") or "admin"))
    return client
