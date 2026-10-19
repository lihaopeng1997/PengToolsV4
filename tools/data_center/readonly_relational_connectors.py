"""Lazy, bounded connectors for relational Agent leases.

This module is deliberately driver-free at import time.  A connector is only
materialized in the worker that owns the resulting read-only lease.  The
provider branches below are intentionally explicit because a connection
timeout applied after a driver has returned cannot bound connection setup.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
import ssl
from typing import Any

from .readonly_driver_adapters import canonical_provider


# One host timeout is a driver hint, not the Agent's total deadline.  Keep it
# bounded to a conservative one day so unit conversion cannot overflow a
# vendor integer field or silently become an effectively unbounded wait.
MAX_TIMEOUT_SECONDS = 86_400.0


class RelationalConnectorError(RuntimeError):
    """Safe connector error; vendor and credential text is never retained."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "CONNECT_FAILED")
        super().__init__(self.code)


_DEFAULT_PORTS = {
    "oracle": 1521,
    "mysql": 3306,
    "oceanbase_oracle": 2883,
    "oceanbase_mysql": 2883,
    "dameng": 5236,
}

_UNSUPPORTED_PROFILE_OPTIONS = frozenset(
    {
        # A raw URI/descriptor can select a different host or retry policy.
        "dsn",
        "uri",
        "url",
        "connection_string",
        "connect_string",
        # SSH ownership is a separate data-center component and cannot be
        # silently ignored by a direct connector.
        "ssh",
        "ssh_tunnel",
        "ssh_host",
        "ssh_port",
        "ssh_username",
        "ssh_user",
        "ssh_password",
        "ssh_password_token",
        "ssh_key",
        "ssh_key_path",
        # TLS/certificate settings have no established persisted profile
        # contract in this connector.  Reject them rather than downgrade or
        # silently discard a user's transport requirement.
        "ssl",
        "tls",
        "ssl_ca",
        "ssl_cert",
        "ssl_key",
        "ssl_key_password",
        "ssl_verify_cert",
        "ssl_verify_identity",
        "ssl_disabled",
        "ssl_server_dn_match",
        "ssl_server_cert_dn",
        "wallet_location",
        "wallet_password",
        "ssl_path",
        "ssl_pwd",
        "protocol",
        "access_token",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_timeout(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise RelationalConnectorError(f"{name.upper()}_INVALID") from None
    if not math.isfinite(result) or result <= 0 or result > MAX_TIMEOUT_SECONDS:
        raise RelationalConnectorError(f"{name.upper()}_INVALID")
    return result


def _ceil_seconds(value: float, name: str) -> int:
    result = int(math.ceil(_positive_timeout(value, name)))
    if result <= 0:
        raise RelationalConnectorError(f"{name.upper()}_INVALID")
    return result


def _milliseconds(value: float, name: str) -> int:
    result = int(math.ceil(_positive_timeout(value, name) * 1000.0))
    if result <= 0:
        raise RelationalConnectorError(f"{name.upper()}_INVALID")
    return result


def _profile_value(profile: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in profile:
            return profile[key]
    return None


def _validate_profile_options(
    profile: Mapping[str, Any], *, allow_data_center_transport: bool = False
) -> None:
    for raw_key in _UNSUPPORTED_PROFILE_OPTIONS:
        if raw_key not in profile:
            continue
        if allow_data_center_transport and raw_key in {
            "ssh_host", "ssh_port", "ssh_username", "ssh_password_token",
        }:
            continue
        value = profile.get(raw_key)
        if value not in (None, "", False, 0, {}, [], ()):
            raise RelationalConnectorError("UNSUPPORTED_TRANSPORT")


def _common_profile(profile: Mapping[str, Any], provider: str) -> tuple[str, int, str, str]:
    host = _text(_profile_value(profile, "host", "server"))
    if not host or "\x00" in host:
        raise RelationalConnectorError("PROFILE_INCOMPLETE")
    try:
        raw_port = _profile_value(profile, "port")
        port = int(raw_port if raw_port not in (None, "") else _DEFAULT_PORTS[provider])
    except (TypeError, ValueError, OverflowError):
        raise RelationalConnectorError("PROFILE_INVALID") from None
    if isinstance(raw_port, bool) or not 1 <= port <= 65535:
        raise RelationalConnectorError("PROFILE_INVALID")
    username = _text(_profile_value(profile, "username", "user"))
    if not username:
        raise RelationalConnectorError("PROFILE_INCOMPLETE")
    database = _text(_profile_value(profile, "database", "schema"))
    return host, port, username, database


def _close_safely(connection: Any, *, ssh_lease: Any = None) -> None:
    try:
        closer = getattr(connection, "close", None)
        if callable(closer):
            closer()
    except Exception:
        if ssh_lease is not None:
            from .connection_transport import LeasedConnection, retain_pending_connection
            retain_pending_connection(LeasedConnection(connection, ssh_lease))
            raise RelationalConnectorError("SSH_CLEANUP_PENDING") from None


def _connect_oracle(
    profile: Mapping[str, Any],
    secret: Any,
    *,
    connect_timeout: float,
    read_timeout: float,
    allow_data_center_transport: bool = False,
    ssh_lease: Any = None,
) -> Any:
    """Open one structured python-oracledb connection.

    Raw descriptors and Easy Connect strings are rejected.  Passing host,
    port, and service_name separately keeps ``tcp_connect_timeout`` effective
    in both Thin and Thick mode and prevents a profile string from replacing
    the fixed host/timeout policy.
    """

    host, port, username, service_name = _common_profile(profile, "oracle")
    if not service_name or not re.fullmatch(r"[A-Za-z0-9_.+$-]+", service_name):
        raise RelationalConnectorError("UNSUPPORTED_ORACLE_DSN")
    _validate_profile_options(profile, allow_data_center_transport=allow_data_center_transport)

    try:
        # Preserve the existing application's process-wide Thin/Thick choice
        # and configured client paths.  Both imports remain inside the worker.
        from tools.oracle_runtime import ensure_oracle_client, load_oracle_paths

        runtime = load_oracle_paths()
        ensure_oracle_client(
            runtime.get("mode", "auto"),
            lib_dir=runtime.get("lib_dir", ""),
            home=runtime.get("home", ""),
            oci_lib=runtime.get("oci_lib", ""),
        )
        import oracledb
    except ImportError:
        raise RelationalConnectorError("ORACLE_DRIVER_UNAVAILABLE") from None
    except RelationalConnectorError:
        raise
    except Exception:
        raise RelationalConnectorError("ORACLE_RUNTIME_UNAVAILABLE") from None

    connection = None
    try:
        # retry_count=0 prevents descriptor-level retry multiplication.  The
        # connection timeout is a driver TCP-setup bound, not a total login
        # or query deadline.
        connection = oracledb.connect(
            user=username,
            password=secret,
            host=host,
            port=port,
            service_name=service_name,
            tcp_connect_timeout=float(connect_timeout),
            retry_count=0,
        )
        # python-oracledb exposes call_timeout in milliseconds.  Set and
        # verify it before ReadOnlyLeaseFactory creates the initialization
        # cursor; this bounds each driver call, not the complete query run.
        timeout_ms = _milliseconds(read_timeout, "read_timeout")
        try:
            getattr(connection, "call_timeout")
            setattr(connection, "call_timeout", timeout_ms)
            if int(getattr(connection, "call_timeout")) != timeout_ms:
                raise AttributeError("call_timeout was not applied")
        except Exception:
            raise RelationalConnectorError("ORACLE_CALL_TIMEOUT_UNAVAILABLE") from None
        return connection
    except RelationalConnectorError:
        _close_safely(connection, ssh_lease=ssh_lease)
        raise
    except Exception:
        _close_safely(connection, ssh_lease=ssh_lease)
        raise RelationalConnectorError("CONNECT_FAILED") from None


def _connect_mysql(
    provider: str,
    profile: Mapping[str, Any],
    secret: Any,
    *,
    connect_timeout: float,
    read_timeout: float,
    transport: Any = None,
    connection_id: str = "",
    profile_revision: str = "",
    ssh_manager: Any = None,
) -> Any:
    host, port, username, database = _common_profile(profile, provider)
    _validate_profile_options(profile, allow_data_center_transport=transport is not None)
    # PyMySQL documents connect_timeout >= 1 second; reject values that the
    # driver would reject rather than invoking it with a surprising default.
    if connect_timeout < 1:
        raise RelationalConnectorError("CONNECT_TIMEOUT_UNSUPPORTED")
    try:
        import pymysql
    except ImportError:
        raise RelationalConnectorError("MYSQL_DRIVER_UNAVAILABLE") from None
    cursor_class = getattr(getattr(pymysql, "cursors", None), "Cursor", None)
    if cursor_class is None:
        raise RelationalConnectorError("MYSQL_DRIVER_UNAVAILABLE")
    connect_kwargs = {
        "host": host,
        "port": port,
        "user": username,
        "password": secret,
        "database": database or None,
        "charset": "utf8mb4",
        "cursorclass": cursor_class,
        "connect_timeout": float(connect_timeout),
        "read_timeout": float(read_timeout),
        "write_timeout": float(read_timeout),
        "autocommit": False,
        "local_infile": False,
    }
    if transport is None:
        try:
            return pymysql.connect(**connect_kwargs)
        except Exception:
            raise RelationalConnectorError("CONNECT_FAILED") from None

    from .connection_transport import (
        ConnectionTransportError,
        DataCenterConnectionTransport,
        LeasedConnection,
        default_ssh_tunnel_manager,
        resolve_secret_token,
    )

    if not isinstance(transport, DataCenterConnectionTransport):
        raise RelationalConnectorError("UNSUPPORTED_TRANSPORT")
    if transport.tls_enabled:
        try:
            # PyMySQL's dictionary-based SSL options disable hostname checks
            # without an explicit CA file. Preserve verification with system
            # trust roots too by passing the fully configured context itself.
            tls_context = ssl.create_default_context(cafile=transport.tls_ca_path or None)
            tls_context.check_hostname = True
            tls_context.verify_mode = ssl.CERT_REQUIRED
            if transport.tls_client_cert_path:
                client_password = (
                    resolve_secret_token(transport.tls_client_key_password_token)
                    if transport.tls_client_key_password_token else None
                )
                tls_context.load_cert_chain(
                    transport.tls_client_cert_path,
                    transport.tls_client_key_path or None,
                    password=client_password,
                )
            connect_kwargs["ssl"] = tls_context
        except ConnectionTransportError as error:
            raise RelationalConnectorError(error.code) from None
        except Exception:
            raise RelationalConnectorError("TLS_CONFIGURATION_INVALID") from None
    elif transport.ssh_enabled:
        # On an SSH tunnel, an explicitly disabled DB TLS policy must not
        # silently become PyMySQL's default opportunistic TLS policy.
        connect_kwargs["ssl_disabled"] = True

    if not transport.ssh_enabled:
        try:
            return pymysql.connect(**connect_kwargs)
        except Exception:
            raise RelationalConnectorError("CONNECT_FAILED") from None

    selected_manager = ssh_manager or default_ssh_tunnel_manager()
    if not connection_id:
        connection_id = str(profile.get("id") or profile.get("connection_id") or "")
    if not profile_revision:
        profile_revision = str(profile.get("profile_revision") or "")
    if not profile_revision:
        try:
            from .host_relational import compute_profile_revision

            profile_revision = compute_profile_revision(profile)
        except Exception:
            raise RelationalConnectorError("PROFILE_INVALID") from None
    lease = None
    local_socket = None
    connection = None
    try:
        lease = selected_manager.acquire(
            transport,
            connection_id=connection_id,
            profile_revision=profile_revision,
        )
        import socket

        local_socket = socket.create_connection(
            (lease.dial_host, lease.dial_port), timeout=float(connect_timeout)
        )
        connect_kwargs["defer_connect"] = True
        connection = pymysql.connect(**connect_kwargs)
        connection.connect(sock=local_socket)
        local_socket = None  # The PyMySQL connection now owns the socket.
        return LeasedConnection(connection, lease)
    except Exception as error:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if local_socket is not None:
            try:
                local_socket.close()
            except Exception:
                pass
        cleanup_complete = True
        if lease is not None:
            try:
                cleanup_complete = bool(lease.release())
            except Exception:
                cleanup_complete = False
        code = getattr(error, "code", "")
        if not cleanup_complete:
            code = "SSH_CLEANUP_PENDING"
        if code:
            raise RelationalConnectorError(str(code)) from None
        raise RelationalConnectorError("CONNECT_FAILED") from None


def _escape_odbc(value: Any) -> str:
    text = "" if value is None else str(value)
    return "{" + text.replace("}", "}}") + "}"


def _translate_numeric_binds(sql: Any, params: Any) -> tuple[Any, Any]:
    """Keep the legacy Oracle-style positional bind behavior for OB ODBC."""

    if not params:
        return sql, params
    sql_text = str(sql)
    if "?" in sql_text and not re.search(r":[1-9]\d*\b", sql_text):
        return sql, params
    pattern = re.compile(r"('(?:''|[^'])*'|\"[^\"]*\")|:([1-9]\d*)\b")
    if isinstance(params, (list, tuple)):
        return pattern.sub(lambda match: match.group(1) if match.group(1) else "?", sql_text), tuple(params)
    if isinstance(params, dict):
        values: list[Any] = []

        def replace(match: re.Match[str]) -> str:
            if match.group(1):
                return match.group(1)
            key = match.group(2)
            if key in params:
                value = params[key]
            elif int(key) in params:
                value = params[int(key)]
            else:
                raise KeyError(key)
            values.append(value)
            return "?"

        return pattern.sub(replace, sql_text), tuple(values)
    return sql, params


class _OceanBaseOdbcCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def execute(self, sql: Any, params: Any = None) -> Any:
        translated_sql, translated_params = _translate_numeric_binds(sql, params)
        if translated_params:
            return self._cursor.execute(translated_sql, translated_params)
        return self._cursor.execute(translated_sql)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _OceanBaseOdbcConnection:
    """Small DB-API wrapper that forwards timeout/autocommit attributes."""

    def __init__(self, connection: Any, driver_name: str) -> None:
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "driver_name", driver_name)

    def cursor(self) -> _OceanBaseOdbcCursor:
        return _OceanBaseOdbcCursor(self._connection.cursor())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_connection", "driver_name"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._connection, name, value)


def _connect_oceanbase_oracle(
    profile: Mapping[str, Any],
    secret: Any,
    *,
    connect_timeout: float,
    read_timeout: float,
    allow_data_center_transport: bool = False,
    ssh_lease: Any = None,
) -> Any:
    if _text(profile.get("oceanbase_oracle_provider")) != "odbc":
        raise RelationalConnectorError("ODBC_SCHEMA_CONFIRM_REQUIRED")
    host, port, username, database = _common_profile(profile, "oceanbase_oracle")
    if not database:
        raise RelationalConnectorError("PROFILE_INCOMPLETE")
    _validate_profile_options(profile, allow_data_center_transport=allow_data_center_transport)
    try:
        import pyodbc
    except ImportError:
        raise RelationalConnectorError("ODBC_DRIVER_UNAVAILABLE") from None

    try:
        drivers = list(pyodbc.drivers() or ())
    except Exception:
        raise RelationalConnectorError("ODBC_DRIVER_UNAVAILABLE") from None
    exact = [str(item) for item in drivers if str(item).casefold() == "oceanbase odbc 2.0 driver"]
    if len(exact) > 1:
        raise RelationalConnectorError("ODBC_DRIVER_AMBIGUOUS")
    driver_name = exact[0] if exact else None
    if driver_name is None:
        fuzzy = [
            str(item)
            for item in drivers
            if "oceanbase" in str(item).casefold() and "odbc" in str(item).casefold()
        ]
        if len(fuzzy) != 1:
            raise RelationalConnectorError(
                "ODBC_DRIVER_AMBIGUOUS" if len(fuzzy) > 1 else "ODBC_DRIVER_UNAVAILABLE"
            )
        driver_name = fuzzy[0]
    if not driver_name:
        raise RelationalConnectorError("ODBC_DRIVER_UNAVAILABLE")

    connection = None
    try:
        connection = pyodbc.connect(
            "".join(
                (
                    f"Driver={{{driver_name}}};",
                    f"Server={_escape_odbc(host)};",
                    f"Port={port};",
                    f"Database={_escape_odbc(database)};",
                    f"User={_escape_odbc(username)};",
                    f"Password={_escape_odbc(secret)};",
                    "Option=3",
                )
            ),
            timeout=_ceil_seconds(connect_timeout, "connect_timeout"),
            autocommit=False,
        )
        # pyodbc's connect(timeout=...) is a login timeout.  The connection
        # timeout is per statement and must be set before lease initialization
        # creates a cursor; require a real attribute and an integer value.
        timeout_value = _ceil_seconds(read_timeout, "read_timeout")
        try:
            getattr(connection, "timeout")
            setattr(connection, "timeout", timeout_value)
            if int(getattr(connection, "timeout")) != timeout_value:
                raise AttributeError("timeout was not applied")
        except Exception:
            raise RelationalConnectorError("ODBC_STATEMENT_TIMEOUT_UNAVAILABLE") from None
        return _OceanBaseOdbcConnection(connection, driver_name)
    except RelationalConnectorError:
        _close_safely(connection, ssh_lease=ssh_lease)
        raise
    except Exception:
        _close_safely(connection, ssh_lease=ssh_lease)
        raise RelationalConnectorError("CONNECT_FAILED") from None


def _connect_dameng(*_: Any, **__: Any) -> Any:
    """Keep DM closed until service-file precedence can be bound safely.

    dmPython documents ``login_timeout`` (milliseconds),
    ``connection_timeout`` (seconds), and ``access_mode``.  Its official
    documentation also states that a service section in ``dm_svc.conf`` can
    override API values.  The current legacy profile has no explicit service
    file/endpoint contract, so using the driver would overstate the fixed
    pre-connect bound.  This is a deliberate fail-closed result.
    """

    raise RelationalConnectorError("DAMENG_SERVICE_TIMEOUT_UNVERIFIED")


def _connect_oracle_tunnel(
    provider: str, profile: Mapping[str, Any], secret: Any, transport: Any,
    *, connect_timeout: float, read_timeout: float, options: Mapping[str, Any],
) -> Any:
    from .connection_transport import LeasedConnection, default_ssh_tunnel_manager
    from .host_relational import compute_profile_revision

    # Validate the fixed service and provider choice before authenticating SSH.
    _host, _port, _username, database = _common_profile(profile, provider)
    if provider == "oracle" and (not database or not re.fullmatch(r"[A-Za-z0-9_.+$-]+", database)):
        raise RelationalConnectorError("UNSUPPORTED_ORACLE_DSN")
    if provider == "oceanbase_oracle":
        if not database:
            raise RelationalConnectorError("PROFILE_INCOMPLETE")
        if _text(profile.get("oceanbase_oracle_provider")) != "odbc":
            raise RelationalConnectorError("ODBC_SCHEMA_CONFIRM_REQUIRED")
    connector = _connect_oracle if provider == "oracle" else _connect_oceanbase_oracle
    manager = options.get("ssh_manager") or default_ssh_tunnel_manager()
    lease = None
    try:
        lease = manager.acquire(
            transport,
            connection_id=str(options.get("connection_id") or profile.get("id") or profile.get("connection_id") or ""),
            profile_revision=str(options.get("profile_revision") or compute_profile_revision(profile)),
        )
        dial_profile = dict(profile, host=lease.dial_host, port=lease.dial_port)
        connection = connector(
            dial_profile, secret, connect_timeout=connect_timeout, read_timeout=read_timeout,
            allow_data_center_transport=True,
            ssh_lease=lease,
        )
        return LeasedConnection(connection, lease)
    except Exception as error:
        if getattr(error, "code", "") == "SSH_CLEANUP_PENDING":
            raise RelationalConnectorError("SSH_CLEANUP_PENDING") from None
        if lease is not None:
            try:
                complete = bool(lease.release())
            except Exception:
                complete = False
            if not complete:
                raise RelationalConnectorError("SSH_CLEANUP_PENDING") from None
        raise RelationalConnectorError(getattr(error, "code", "") or "CONNECT_FAILED") from None


def connect_relational(
    provider: Any,
    profile: Mapping[str, Any],
    secret: Any,
    **options: Any,
) -> Any:
    """Open one dedicated connection for a fixed relational lease target."""

    if not isinstance(profile, Mapping):
        raise RelationalConnectorError("PROFILE_INVALID")
    nested = options.get("options")
    nested_options = nested if isinstance(nested, Mapping) else {}
    mode = _text(profile.get("mode")) or _text(options.get("mode"))
    try:
        selected_provider = canonical_provider(provider, mode)
    except Exception:
        raise RelationalConnectorError("UNSUPPORTED_COMBINATION") from None
    connect_timeout = _positive_timeout(
        options.get("connect_timeout", nested_options.get("connect_timeout")),
        "connect_timeout",
    )
    read_timeout = _positive_timeout(
        options.get("read_timeout", nested_options.get("read_timeout")),
        "read_timeout",
    )
    from .connection_transport import ConnectionTransportError, DataCenterConnectionTransport

    try:
        transport = DataCenterConnectionTransport.from_profile(profile, selected_provider, mode)
    except ConnectionTransportError as error:
        raise RelationalConnectorError(error.code) from None
    _validate_profile_options(profile, allow_data_center_transport=transport is not None)
    if selected_provider in {"oracle", "oceanbase_oracle"} and transport is not None:
        return _connect_oracle_tunnel(
            selected_provider, profile, secret, transport,
            connect_timeout=connect_timeout, read_timeout=read_timeout, options=options,
        )
    if selected_provider == "oracle":
        return _connect_oracle(
            profile,
            secret,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
    if selected_provider in {"mysql", "oceanbase_mysql"}:
        return _connect_mysql(
            selected_provider,
            profile,
            secret,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            transport=transport,
            connection_id=str(options.get("connection_id") or profile.get("id") or profile.get("connection_id") or ""),
            profile_revision=str(options.get("profile_revision") or profile.get("profile_revision") or ""),
            ssh_manager=options.get("ssh_manager"),
        )
    if selected_provider == "oceanbase_oracle":
        return _connect_oceanbase_oracle(
            profile,
            secret,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
    if selected_provider == "dameng":
        return _connect_dameng(profile, secret, connect_timeout=connect_timeout, read_timeout=read_timeout)
    raise RelationalConnectorError("UNSUPPORTED_COMBINATION")


__all__ = [
    "RelationalConnectorError",
    "connect_relational",
]
