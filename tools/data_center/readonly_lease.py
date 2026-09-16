"""Credential-free, worker-created read-only database lease.

``ReadOnlyLeaseFactory`` is the only object a data-center session needs to
retain.  It stores a fixed target snapshot and opaque loader callables.  The
profile, secret, and raw connection exist only during the worker-side
``connect`` call; the lease representation and query results never include
them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import inspect
from typing import Any, Callable

from .readonly_driver_adapters import (
    CancellationStatus,
    DBAPIReadOnlyAdapter,
    ReadOnlyCancelled,
    ReadOnlyClosedError,
    ReadOnlyDriverCapabilities,
    ReadOnlyDriverError,
    ReadOnlyInitializationError,
    ReadOnlyQueryError,
    ReadOnlyResultUnknown,
    ReadOnlyThreadOwnershipError,
    ReadOnlyTimeout,
    canonical_provider,
    create_readonly_driver_adapter,
)


class ReadOnlyLeaseError(RuntimeError):
    """Safe host error; it deliberately omits profile and driver details."""

    code = "READONLY_LEASE_ERROR"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.code = str(code or self.code)
        super().__init__(str(message or self.code))


class ReadOnlyProfileError(ReadOnlyLeaseError):
    code = "PROFILE_UNAVAILABLE"


class ReadOnlySecretError(ReadOnlyLeaseError):
    code = "SECRET_UNAVAILABLE"


class ReadOnlyScopeError(ReadOnlyLeaseError):
    code = "SCOPE_DENIED"


_PROVIDER_DIALECTS = {
    "oracle": "oracle",
    "mysql": "mysql",
    "oceanbase_oracle": "oracle",
    "oceanbase_mysql": "mysql",
    "dameng": "oracle",
}


def _canonical_mode(mode: Any, provider: str) -> str:
    """Normalize the small provider-mode vocabulary used by a lease target."""

    raw = str(mode or "").strip().casefold().replace("-", "_")
    if raw == "mysql":
        normalized = "mysql"
    elif raw == "oracle":
        normalized = "dameng" if provider == "dameng" else "oracle"
    elif raw in {"standalone", "cluster"}:
        if provider not in {"oceanbase_oracle", "oceanbase_mysql"}:
            raise ValueError("provider and mode do not match")
        normalized = "oracle"
    elif raw in {"dameng", "dm", "dm8"}:
        normalized = "dameng"
    else:
        normalized = ""
    if not normalized:
        normalized = {
            "mysql": "mysql",
            "oceanbase_mysql": "mysql",
            "oracle": "oracle",
            "oceanbase_oracle": "oracle",
            "dameng": "dameng",
        }.get(provider, "")
    expected = {
        "mysql": "mysql",
        "oceanbase_mysql": "mysql",
        "oracle": "oracle",
        "oceanbase_oracle": "oracle",
        "dameng": "dameng",
    }.get(provider, "")
    if normalized != expected:
        raise ValueError("provider and mode do not match")
    return normalized


def _canonical_dialect(dialect: Any, provider: str, mode: str) -> str:
    """Normalize a profile/target dialect to its parser dialect."""

    raw = str(dialect or "").strip().casefold().replace("-", "_")
    if raw in {"oceanbase", "ob"}:
        if provider not in {"oceanbase_oracle", "oceanbase_mysql"}:
            raise ValueError("provider and dialect do not match")
        raw = mode
    elif raw == "oceanbase_mysql":
        if provider != "oceanbase_mysql":
            raise ValueError("provider and dialect do not match")
        raw = "mysql"
    elif raw == "oceanbase_oracle":
        if provider != "oceanbase_oracle":
            raise ValueError("provider and dialect do not match")
        raw = "oracle"
    elif raw == "mysql":
        raw = "mysql"
    elif raw == "oracle":
        raw = "oracle"
    elif raw in {"dameng", "dm", "dm8"}:
        if provider != "dameng":
            raise ValueError("provider and dialect do not match")
        raw = "oracle"
    expected = _PROVIDER_DIALECTS.get(provider, "")
    if not raw:
        raw = expected
    if raw != expected:
        raise ValueError("provider and dialect do not match")
    return raw


@dataclass(frozen=True, slots=True)
class ReadOnlyTarget:
    """Immutable lease scope; credentials and addresses are intentionally absent."""

    connection_id: str
    profile_revision: str
    provider: str
    dialect: str = ""
    mode: str = ""

    def __post_init__(self) -> None:
        connection_id = str(self.connection_id or "").strip()
        profile_revision = str(self.profile_revision or "").strip()
        if not connection_id:
            raise ValueError("connection_id must be non-empty")
        if not profile_revision:
            raise ValueError("profile_revision must be non-empty")
        raw_dialect = str(self.dialect or "").strip().casefold()
        raw_mode = str(self.mode or "").strip().casefold()
        provider = canonical_provider(self.provider, raw_mode or (raw_dialect if raw_dialect == "mysql" else None))
        mode = _canonical_mode(raw_mode, provider)
        dialect = _canonical_dialect(raw_dialect, provider, mode)
        object.__setattr__(self, "connection_id", connection_id)
        object.__setattr__(self, "profile_revision", profile_revision)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "dialect", dialect)
        object.__setattr__(self, "mode", mode)

    def __repr__(self) -> str:
        return (
            "ReadOnlyTarget("
            f"connection_id={self.connection_id!r}, "
            f"profile_revision={self.profile_revision!r}, "
            f"provider={self.provider!r}, mode={self.mode!r}, dialect={self.dialect!r})"
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "connection_id": self.connection_id,
            "profile_revision": self.profile_revision,
            "provider": self.provider,
            "mode": self.mode,
            "dialect": self.dialect,
        }


ReadOnlyConnectionSnapshot = ReadOnlyTarget
ReadOnlyLeaseSpec = ReadOnlyTarget
ReadonlyTarget = ReadOnlyTarget


def _call_declared(callable_value: Callable[..., Any], values: Mapping[str, Any], fallback: tuple[Any, ...] = ()) -> Any:
    """Adapt small host fakes without passing profile data to unrelated args."""

    try:
        signature = inspect.signature(callable_value)
    except (TypeError, ValueError):
        return callable_value(*fallback)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        kwargs = {
            "provider": values.get("provider"),
            "mode": values.get("mode"),
            "dialect": values.get("dialect"),
            "profile": values.get("profile"),
            "secret": values.get("secret"),
            "connection_id": values.get("connection_id"),
            "read_only": True,
            "autocommit": False,
            **dict(values.get("options", {})),
        }
        return callable_value(**kwargs)
    positional: list[Any] = []
    fallback_index = 0
    for item in params:
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            if item.name in values:
                positional.append(values[item.name])
            elif fallback_index < len(fallback):
                positional.append(fallback[fallback_index])
                fallback_index += 1
            elif item.default is inspect.Parameter.empty:
                raise TypeError("unsupported host callable signature")
    kwargs = {
        item.name: values[item.name]
        for item in params
        if item.kind == inspect.Parameter.KEYWORD_ONLY and item.name in values
    }
    return callable_value(*positional, **kwargs)


def _safe_error(error_type: type[ReadOnlyLeaseError], code: str) -> ReadOnlyLeaseError:
    return error_type(code=code)


_MISSING_PROFILE_VALUE = object()


def _profile_value(profile: Any, key: str) -> Any:
    if isinstance(profile, Mapping):
        try:
            return profile.get(key, _MISSING_PROFILE_VALUE)
        except Exception:
            return _MISSING_PROFILE_VALUE
    try:
        return getattr(profile, key)
    except Exception:
        return _MISSING_PROFILE_VALUE


class ReadOnlyLease:
    """Worker-owned adapter lease returned by :class:`ReadOnlyLeaseFactory`."""

    def __init__(self, target: ReadOnlyTarget, adapter: DBAPIReadOnlyAdapter) -> None:
        self.target = target
        self._adapter = adapter

    def __repr__(self) -> str:
        return f"ReadOnlyLease(target={self.target!r}, state={self.state!r})"

    @property
    def state(self) -> str:
        return self._adapter.state

    @property
    def closed(self) -> bool:
        return self._adapter.closed

    @property
    def initialized(self) -> bool:
        return self._adapter.initialized

    @property
    def provider(self) -> str:
        return self.target.provider

    @property
    def mode(self) -> str:
        return self.target.mode

    @property
    def dialect(self) -> str:
        return self.target.dialect

    @property
    def capabilities(self) -> ReadOnlyDriverCapabilities:
        return self._adapter.capabilities

    @property
    def supports_cancel(self) -> bool:
        return self._adapter.supports_cancel

    def execute_readonly(
        self,
        query: Any = None,
        parameters: Any = None,
        row_limit: int = 100,
        deadline: float | None = None,
        cancellation: Any = None,
        cancel_token: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return self._adapter.execute_readonly(
            query,
            parameters,
            row_limit,
            deadline,
            cancellation,
            cancel_token,
        )

    execute_read_only = execute_readonly
    query_readonly = execute_readonly
    query_read_only = execute_readonly

    def cancel(self) -> CancellationStatus:
        return self._adapter.cancel()

    request_cancel = cancel

    def close(self) -> None:
        self._adapter.close()

    disconnect = close


class ReadOnlyLeaseFactory:
    """Create one isolated lease in the calling worker thread.

    ``profile_loader``, ``secret_loader``, and ``connector`` are all invoked
    lazily.  The factory never reads the legacy config module and never keeps
    the loaded profile, secret, or connection after ``connect`` returns.
    """

    def __init__(
        self,
        target: ReadOnlyTarget | Mapping[str, Any] | None = None,
        *,
        connection_id: str | None = None,
        profile_revision: str = "",
        provider: str | None = None,
        mode: str | None = None,
        profile_loader: Callable[..., Any],
        secret_loader: Callable[..., Any],
        connector: Callable[..., Any] | Any,
        max_row_limit: int = 100,
    ) -> None:
        if target is None:
            if connection_id is None or provider is None:
                raise ValueError("target or connection_id/provider is required")
            target = ReadOnlyTarget(connection_id, profile_revision, provider, mode=mode or "")
        elif not isinstance(target, ReadOnlyTarget):
            data = dict(target)
            raw_provider = data.get("provider", data.get("engine", data.get("dialect", provider)))
            target = ReadOnlyTarget(
                data.get("connection_id", data.get("id", connection_id)),
                data.get("profile_revision", data.get("revision", profile_revision)),
                raw_provider,
                data.get("dialect", ""),
                data.get("mode", mode) or "",
            )
        if not callable(profile_loader) or not callable(secret_loader):
            raise TypeError("profile_loader and secret_loader must be callable")
        if not callable(connector) and not callable(getattr(connector, "connect", None)):
            raise TypeError("connector must be callable or expose connect")
        self.target = target
        self.profile_loader = profile_loader
        self.secret_loader = secret_loader
        self.connector = connector
        self.max_row_limit = max_row_limit

    def __repr__(self) -> str:
        return f"ReadOnlyLeaseFactory(target={self.target!r})"

    def __call__(
        self,
        connection_id: str | None = None,
        *,
        session_id: str | None = None,
        generation: int | None = None,
        **kwargs: Any,
    ) -> ReadOnlyLease:
        return self.connect(
            connection_id,
            session_id=session_id,
            generation=generation,
            **kwargs,
        )

    def connect(
        self,
        connection_id: str | None = None,
        *,
        session_id: str | None = None,
        generation: int | None = None,
        **kwargs: Any,
    ) -> ReadOnlyLease:
        supplied_connection = str(connection_id or "").strip()
        if supplied_connection and supplied_connection != self.target.connection_id:
            raise ReadOnlyScopeError(code="SCOPE_DENIED")
        # The lease target is immutable.  No free-form connection/profile
        # override is meaningful here, so reject every extra keyword instead
        # of silently ignoring a future target field.
        if kwargs:
            raise ReadOnlyScopeError(code="SCOPE_DENIED")

        profile: Any = None
        secret: Any = None
        connection: Any = None
        try:
            profile = _call_declared(
                self.profile_loader,
                {
                    "connection_id": self.target.connection_id,
                    "profile_revision": self.target.profile_revision,
                    "provider": self.target.provider,
                    "mode": self.target.mode,
                    "dialect": self.target.dialect,
                    "target": self.target,
                },
                fallback=(self.target.connection_id, self.target.profile_revision),
            )
        except Exception:
            raise _safe_error(ReadOnlyProfileError, "PROFILE_UNAVAILABLE") from None
        if profile is None:
            raise _safe_error(ReadOnlyProfileError, "PROFILE_UNAVAILABLE")
        self._verify_profile(profile)
        try:
            secret = _call_declared(
                self.secret_loader,
                {
                    "profile": profile,
                    "connection_id": self.target.connection_id,
                    "profile_revision": self.target.profile_revision,
                    "provider": self.target.provider,
                    "mode": self.target.mode,
                    "dialect": self.target.dialect,
                    "target": self.target,
                },
                fallback=(profile,),
            )
        except Exception:
            raise _safe_error(ReadOnlySecretError, "SECRET_UNAVAILABLE") from None
        if secret is None:
            raise _safe_error(ReadOnlySecretError, "SECRET_UNAVAILABLE")

        strategy_options = {
            "read_only": True,
            "autocommit": False,
            **dict(self._adapter_options()),
        }
        connector = self.connector if callable(self.connector) else getattr(self.connector, "connect")
        try:
            connection = _call_declared(
                connector,
                {
                    "provider": self.target.provider,
                    "engine": self.target.provider,
                    "mode": self.target.mode,
                    "dialect": self.target.dialect,
                    "profile": profile,
                    "connection_profile": profile,
                    "secret": secret,
                    "password": secret,
                    "credentials": secret,
                    "connection_id": self.target.connection_id,
                    "session_id": session_id,
                    "generation": generation,
                    "read_only": True,
                    "readonly": True,
                    "autocommit": False,
                    "autoCommit": False,
                    "access_mode": strategy_options.get("access_mode"),
                    "options": strategy_options,
                },
                fallback=(self.target.provider, profile, secret),
            )
        except Exception:
            raise ReadOnlyLeaseError(code="CONNECT_FAILED") from None
        if connection is None:
            raise ReadOnlyLeaseError(code="CONNECT_FAILED")
        try:
            adapter = create_readonly_driver_adapter(
                self.target.provider,
                connection,
                max_row_limit=self.max_row_limit,
            )
            return ReadOnlyLease(self.target, adapter)
        except ReadOnlyDriverError as exc:
            # Do not expose vendor exception text or a profile/secret repr.
            if isinstance(exc, ReadOnlyInitializationError):
                raise ReadOnlyInitializationError(code="READONLY_INIT_FAILED") from None
            raise ReadOnlyLeaseError(code="READONLY_INIT_FAILED") from None
        finally:
            # These names are intentionally not assigned to the returned lease.
            profile = None
            secret = None
            connection = None

    open = connect
    create = connect

    def _adapter_options(self) -> Mapping[str, Any]:
        from .readonly_driver_adapters import READONLY_DRIVER_MATRIX

        return READONLY_DRIVER_MATRIX[self.target.provider].connector_options

    def _verify_profile(self, profile: Any) -> None:
        """Bind every loaded target field before a secret or connection call."""

        if profile is None:
            raise ReadOnlyProfileError(code="PROFILE_MISMATCH")
        required = ("connection_id", "profile_revision", "provider", "mode", "dialect")
        values = {key: _profile_value(profile, key) for key in required}
        if any(value is _MISSING_PROFILE_VALUE or not str(value or "").strip() for value in values.values()):
            raise ReadOnlyScopeError(code="PROFILE_MISMATCH")

        loaded_connection = str(values["connection_id"]).strip()
        loaded_revision = str(values["profile_revision"]).strip()
        if loaded_connection != self.target.connection_id or loaded_revision != self.target.profile_revision:
            raise ReadOnlyScopeError(code="PROFILE_MISMATCH")

        try:
            loaded_provider = canonical_provider(values["provider"], values["mode"])
            loaded_mode = _canonical_mode(values["mode"], loaded_provider)
            loaded_dialect = _canonical_dialect(values["dialect"], loaded_provider, loaded_mode)
        except Exception:
            raise ReadOnlyScopeError(code="PROFILE_MISMATCH") from None
        if (
            loaded_provider != self.target.provider
            or loaded_mode != self.target.mode
            or loaded_dialect != self.target.dialect
        ):
            raise ReadOnlyScopeError(code="PROFILE_MISMATCH")


ReadOnlyConnectionFactory = ReadOnlyLeaseFactory
ReadOnlyDriverFactory = ReadOnlyLeaseFactory
ReadonlyLease = ReadOnlyLease
ReadonlyLeaseFactory = ReadOnlyLeaseFactory
create_readonly_lease_factory = ReadOnlyLeaseFactory


__all__ = [
    "CancellationStatus",
    "ReadOnlyConnectionFactory",
    "ReadOnlyConnectionSnapshot",
    "ReadOnlyDriverCapabilities",
    "ReadOnlyDriverError",
    "ReadOnlyDriverFactory",
    "ReadOnlyInitializationError",
    "ReadOnlyLease",
    "ReadOnlyLeaseError",
    "ReadOnlyLeaseFactory",
    "ReadOnlyLeaseSpec",
    "ReadOnlyProfileError",
    "ReadOnlyQueryError",
    "ReadOnlyResultUnknown",
    "ReadOnlyScopeError",
    "ReadOnlySecretError",
    "ReadOnlyTarget",
    "ReadOnlyThreadOwnershipError",
    "ReadOnlyTimeout",
    "ReadonlyLease",
    "ReadonlyLeaseFactory",
    "ReadonlyTarget",
    "create_readonly_lease_factory",
]
