"""Host adapter from the legacy connection store to read-only leases.

The data-center Agent must not use the SQL editor's connection or its
console execution path.  This module keeps that boundary explicit:
one selected connection id is resolved lazily, a read-only lease gets a
private connection from a host connector, and credentials are decrypted only
while that connector call is on the worker stack.

The default connection/profile loader is resolved lazily as well.  Tests and
future hosts can inject an in-memory loader and connector without importing a
database driver or touching the user's configuration file at import time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
import threading
from typing import Any, Callable

from .readonly_driver_adapters import canonical_provider
from .readonly_lease import (
    ReadOnlyLease,
    ReadOnlyLeaseError,
    ReadOnlyLeaseFactory,
    ReadOnlyProfileError,
    ReadOnlyScopeError,
    ReadOnlySecretError,
    ReadOnlyTarget,
)


class RelationalHostError(ReadOnlyLeaseError):
    """Safe error raised before a relational Agent connection is opened."""

    code = "RELATIONAL_HOST_ERROR"


class RelationalProfileError(ReadOnlyProfileError, RelationalHostError):
    """The selected connection profile is absent or not usable."""

    code = "PROFILE_UNAVAILABLE"


class RelationalSecretError(ReadOnlySecretError, RelationalHostError):
    """The selected profile's credential could not be made available."""

    code = "SECRET_UNAVAILABLE"


class RelationalScopeError(ReadOnlyScopeError, RelationalHostError):
    """The selected profile does not match the fixed lease target."""

    code = "PROFILE_MISMATCH"


@dataclass(frozen=True, slots=True)
class RelationalProfileSnapshot:
    """Public-safe metadata for one selected connection profile.

    Host, database, username, password tokens, SSH settings, and driver
    details are deliberately absent.  The snapshot is suitable for binding a
    session or a run context, but not for sending to a model.
    """

    connection_id: str
    profile_revision: str
    provider: str
    mode: str
    dialect: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "connection_id", _required_text(self.connection_id, "connection_id"))
        object.__setattr__(self, "profile_revision", _required_text(self.profile_revision, "profile_revision"))
        object.__setattr__(self, "provider", _required_text(self.provider, "provider"))
        object.__setattr__(self, "mode", _required_text(self.mode, "mode"))
        object.__setattr__(self, "dialect", _required_text(self.dialect, "dialect"))

    def as_dict(self) -> dict[str, str]:
        return {
            "connection_id": self.connection_id,
            "profile_revision": self.profile_revision,
            "provider": self.provider,
            "mode": self.mode,
            "dialect": self.dialect,
        }


_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "passphrase",
    "cookie",
)
_REVISION_IGNORED_KEYS = frozenset(
    {
        "profile_revision",
        "revision",
        "name",
        "label",
        "display_name",
        "group_path",
        "environment",
        "description",
    }
)
_SECRET_MISSING = object()


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _is_secret_key(key: Any) -> bool:
    text = str(key or "").strip().casefold().replace("-", "_")
    return any(part in text for part in _SECRET_KEY_PARTS)


def _canonical_for_revision(value: Any, *, key: str = "") -> Any:
    """Make a JSON-safe, deterministic revision payload.

    Credential values are represented by a one-way digest.  This allows a
    password-token rotation to invalidate an old run without decrypting the
    token or putting the token itself into a target, error, or result.
    """

    if _is_secret_key(key):
        if value in (None, ""):
            return None
        try:
            raw = str(value).encode("utf-8", errors="surrogatepass")
        except Exception:
            raw = b"<secret>"
        return {"secret_sha256": hashlib.sha256(raw).hexdigest()}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key in sorted((str(item) for item in value.keys()), key=lambda item: (item.casefold(), item)):
            if raw_key.casefold() in _REVISION_IGNORED_KEYS:
                continue
            try:
                child = value.get(raw_key)
            except Exception:
                child = None
            result[raw_key] = _canonical_for_revision(child, key=raw_key)
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical_for_revision(item, key=key) for item in value]
    if isinstance(value, (set, frozenset)):
        values = [_canonical_for_revision(item, key=key) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return str(value)


def compute_profile_revision(profile: Mapping[str, Any]) -> str:
    """Compute a stable, non-secret revision for a connection profile.

    The result is independent of mapping insertion order and excludes display
    metadata.  Encrypted or legacy password fields affect the digest through a
    one-way hash, so callers never need to decrypt a credential to bind a
    profile version.
    """

    if not isinstance(profile, Mapping):
        raise ValueError("profile must be a mapping")
    payload = _canonical_for_revision(dict(profile))
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _invoke_declared(
    callable_value: Callable[..., Any],
    values: Mapping[str, Any],
    *,
    fallback: tuple[Any, ...] = (),
) -> Any:
    """Call an injected host hook using only its declared arguments."""

    try:
        signature = inspect.signature(callable_value)
    except (TypeError, ValueError):
        return callable_value(*fallback)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params):
        # Do not indiscriminately copy a profile into arbitrary kwargs.  The
        # named values below are the small host contract, and connector
        # options are already bounded by the provider matrix.
        kwargs = {
            key: values[key]
            for key in values
            if key not in {"connection_profile", "credentials", "password"}
        }
        return callable_value(**kwargs)
    positional: list[Any] = []
    fallback_index = 0
    for item in params:
        if item.kind not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            continue
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


def _required_profile_value(profile: Mapping[str, Any], *keys: str) -> Any:
    values: list[Any] = []
    for key in keys:
        try:
            value = profile.get(key, _SECRET_MISSING)
        except Exception:
            value = _SECRET_MISSING
        if value is not _SECRET_MISSING and str(value or "").strip():
            values.append(value)
    if not values:
        return _SECRET_MISSING
    first = str(values[0]).strip()
    if any(str(value).strip() != first for value in values[1:]):
        raise RelationalScopeError(code="PROFILE_MISMATCH")
    return values[0]


def _select_profile(source: Any, connection_id: str) -> Mapping[str, Any]:
    """Select exactly one profile for the requested id from a source."""

    if source is None:
        raise RelationalProfileError(code="PROFILE_UNAVAILABLE")
    if isinstance(source, Mapping):
        # A mapping can itself be one profile or a connection-id -> profile
        # registry.  Treat an explicit id/connection_id field as a profile.
        profile_id = source.get("connection_id", source.get("id", _SECRET_MISSING))
        if profile_id is not _SECRET_MISSING:
            candidates = [source]
        elif connection_id in source:
            candidate = source[connection_id]
            # A compact in-memory registry may use its key as the only
            # connection id.  Materialize that id on a shallow copy so the
            # canonicalizer can still verify an explicit scope field without
            # mutating the caller's configuration.
            if isinstance(candidate, Mapping) and not any(
                str(candidate.get(key) or "").strip() for key in ("connection_id", "id")
            ):
                candidate = dict(candidate)
                candidate["id"] = connection_id
            candidates = [candidate]
        elif isinstance(source.get("items"), Sequence) and not isinstance(source.get("items"), (str, bytes, bytearray)):
            candidates = list(source.get("items") or ())
        else:
            candidates = []
    elif isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray)):
        candidates = list(source)
    else:
        try:
            candidates = list(source)
        except (TypeError, ValueError):
            candidates = [source]

    matches: list[Mapping[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = candidate.get("connection_id", candidate.get("id", ""))
        if str(candidate_id or "").strip() == connection_id:
            matches.append(candidate)
    if len(matches) != 1:
        raise RelationalProfileError(
            code="PROFILE_NOT_FOUND" if not matches else "PROFILE_AMBIGUOUS"
        )
    return matches[0]


def _default_connections_loader() -> Any:
    """Load the legacy store only when a worker actually needs a profile."""

    from tools.db_connect import load_connections

    return load_connections()


def _default_decryptor(token: Any) -> str:
    from tools.secure_store import decrypt_secret

    return decrypt_secret(str(token or ""))


def _secret_token(profile: Mapping[str, Any]) -> Any:
    # ``password`` is the current connections.json spelling.  The aliases
    # keep migration and injected test profiles explicit without accepting a
    # free-form secret from model arguments.
    for key in (
        "password",
        "password_ciphertext",
        "password_token",
        "secret_ciphertext",
        "secret_token",
        "credential_token",
    ):
        if key in profile:
            return profile.get(key)
    return ""


def _decrypt_profile_secret(profile: Mapping[str, Any], *, decryptor: Callable[[Any], Any]) -> Any:
    token = _secret_token(profile)
    if token in (None, ""):
        return ""
    try:
        secret = decryptor(token)
    except Exception:
        raise RelationalSecretError(code="SECRET_UNAVAILABLE") from None
    if secret is None:
        raise RelationalSecretError(code="SECRET_UNAVAILABLE")
    # secure_store returns an empty string for an invalid encrypted token.
    # Treat that as unavailable when a non-empty token was supplied; an empty
    # password remains valid when the stored token itself is empty.
    if str(secret) == "" and str(token) != "":
        raise RelationalSecretError(code="SECRET_UNAVAILABLE")
    return secret


def _legacy_connector(provider: str, profile: Mapping[str, Any], secret: Any, **options: Any) -> Any:
    """Reject the legacy connector until a dedicated timeout-aware hook exists.

    The legacy connection helper does not accept a connection timeout.  A
    timeout attribute assigned after it returns cannot bound the establishment
    phase, so silently using that helper would overstate the host boundary.
    Hosts must inject a connector whose connection-timeout contract is known.
    """

    del provider, profile, secret, options
    raise RelationalHostError(code="CONNECTOR_UNAVAILABLE")


def _canonical_profile(
    profile: Mapping[str, Any],
    connection_id: str,
    *,
    expected_revision: str | None = None,
    expected_provider: str | None = None,
    expected_mode: str | None = None,
    expected_dialect: str | None = None,
) -> tuple[dict[str, Any], ReadOnlyTarget]:
    """Normalize and verify one profile into a fixed target snapshot."""

    if not isinstance(profile, Mapping):
        raise RelationalProfileError(code="PROFILE_INVALID")
    expected_revision = _optional_text(expected_revision)
    expected_provider = _optional_text(expected_provider)
    expected_mode = _optional_text(expected_mode)
    expected_dialect = _optional_text(expected_dialect)
    selected_id = _required_text(connection_id, "connection_id")
    data = dict(profile)
    raw_id = _required_profile_value(data, "connection_id", "id")
    if raw_id is _SECRET_MISSING or str(raw_id).strip() != selected_id:
        raise RelationalScopeError(code="PROFILE_MISMATCH")

    try:
        # Persisted revision fields are metadata from the legacy store and are
        # deliberately excluded by ``compute_profile_revision``.  The target
        # must follow the current connection content, including credential
        # token changes, instead of trusting an arbitrary old field.
        revision = compute_profile_revision(data)
    except Exception:
        raise RelationalProfileError(code="PROFILE_INVALID") from None
    if expected_revision is not None and str(expected_revision).strip() != revision:
        raise RelationalScopeError(code="PROFILE_MISMATCH")

    raw_dialect = data.get("dialect") or data.get("driver_dialect", "")
    raw_mode = data.get("mode") or ""
    raw_provider = data.get("provider") or data.get("engine", "")
    if not str(raw_provider or "").strip():
        raw_provider = raw_dialect
    if not str(raw_provider or "").strip() or not str(raw_dialect or "").strip():
        raise RelationalProfileError(code="PROFILE_INCOMPLETE")
    try:
        provider = canonical_provider(raw_provider, raw_mode)
        target = ReadOnlyTarget(
            selected_id,
            revision,
            provider,
            str(raw_dialect or ""),
            str(raw_mode or ""),
        )
        expected_target = None
        if any(value is not None for value in (expected_provider, expected_mode, expected_dialect)):
            expected_raw_provider = expected_provider or expected_dialect
            if not str(expected_raw_provider or "").strip():
                raise ValueError("expected provider is missing")
            expected_target = ReadOnlyTarget(
                selected_id,
                revision,
                canonical_provider(expected_raw_provider, expected_mode),
                str(expected_dialect or ""),
                str(expected_mode or ""),
            )
    except Exception:
        raise RelationalProfileError(code="UNSUPPORTED_COMBINATION") from None
    if expected_target is not None and (
        target.provider != expected_target.provider
        or target.mode != expected_target.mode
        or target.dialect != expected_target.dialect
    ):
        raise RelationalScopeError(code="PROFILE_MISMATCH")
    if target.provider not in {"oracle", "mysql", "oceanbase_oracle", "oceanbase_mysql", "dameng"}:
        raise RelationalProfileError(code="UNSUPPORTED_COMBINATION")

    # The normalized fields are the only profile values ReadOnlyLeaseFactory
    # trusts for target binding.  Other connection options remain private to
    # the connector closure and never enter the public snapshot.
    data["id"] = selected_id
    data["connection_id"] = selected_id
    data["profile_revision"] = revision
    data["provider"] = target.provider
    data["mode"] = target.mode
    data["dialect"] = target.dialect
    return data, target


class LazyRelationalLeaseFactory:
    """Callable SessionManager factory that materializes one lease target lazily."""

    def __init__(
        self,
        host: "RelationalReadOnlyHost",
        connection_id: str,
        *,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        dialect: str | None = None,
    ) -> None:
        self._host = host
        self.connection_id = _required_text(connection_id, "connection_id")
        self._expected_revision = _optional_text(profile_revision)
        self._expected_provider = provider
        self._expected_mode = mode
        self._expected_dialect = dialect
        self._factory: ReadOnlyLeaseFactory | None = None
        self._target: ReadOnlyTarget | None = None
        self._lock = threading.RLock()

    @property
    def target(self) -> ReadOnlyTarget | None:
        with self._lock:
            return self._target

    def __repr__(self) -> str:
        target = self.target
        return f"{type(self).__name__}(connection_id={self.connection_id!r}, target={target!r})"

    def __call__(
        self,
        connection_id: str | None = None,
        *,
        session_id: str | None = None,
        generation: int | None = None,
        **kwargs: Any,
    ) -> ReadOnlyLease:
        supplied = str(connection_id or "").strip()
        if supplied and supplied != self.connection_id:
            raise RelationalScopeError(code="SCOPE_DENIED")
        if kwargs:
            raise RelationalScopeError(code="SCOPE_DENIED")
        factory = self._materialize()
        return factory(self.connection_id, session_id=session_id, generation=generation)

    connect = __call__

    def _materialize(self) -> ReadOnlyLeaseFactory:
        with self._lock:
            if self._factory is not None:
                return self._factory
            profile = self._host._load_profile(self.connection_id)
            normalized, target = _canonical_profile(
                profile,
                self.connection_id,
                expected_revision=self._expected_revision,
                expected_provider=self._expected_provider,
                expected_mode=self._expected_mode,
                expected_dialect=self._expected_dialect,
            )
            self._target = target
            self._factory = self._host._build_factory(
                target,
                initial_profile=normalized,
            )
            return self._factory


class RelationalReadOnlyHost:
    """Create isolated relational read-only lease factories by connection id."""

    def __init__(
        self,
        profile: Mapping[str, Any] | None = None,
        *,
        profile_loader: Callable[..., Any] | Mapping[str, Any] | None = None,
        loader: Callable[..., Any] | Mapping[str, Any] | None = None,
        connections_loader: Callable[..., Any] | Mapping[str, Any] | Sequence[Any] | None = None,
        connections: Mapping[str, Any] | Sequence[Any] | None = None,
        connection_id: str | None = None,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        dialect: str | None = None,
        secret_loader: Callable[..., Any] | None = None,
        decryptor: Callable[[Any], Any] | None = None,
        connector: Callable[..., Any] | Any | None = None,
        connection_timeout_seconds: float = 8.0,
        read_timeout_seconds: float = 15.0,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        max_row_limit: int = 100,
    ) -> None:
        if profile_loader is not None and loader is not None:
            raise TypeError("provide profile_loader or loader, not both")
        if profile_loader is None:
            profile_loader = loader
        if profile is not None and (profile_loader is not None or connections_loader is not None or connections is not None):
            raise TypeError("provide profile or a profile/connection loader, not both")
        if profile_loader is not None and (connections_loader is not None or connections is not None):
            raise TypeError("provide profile_loader or connections_loader, not both")
        if connections_loader is not None and connections is not None:
            raise TypeError("provide connections_loader or connections, not both")
        if profile_loader is not None and not callable(profile_loader) and not isinstance(profile_loader, Mapping):
            raise TypeError("profile_loader must be callable or a mapping")
        if connections_loader is not None and not callable(connections_loader) and not isinstance(connections_loader, Mapping):
            raise TypeError("connections_loader must be callable or a mapping")
        if connector is not None and not callable(connector) and not callable(getattr(connector, "connect", None)):
            # A raw DB-API connection is a manual session, not a connector.
            raise TypeError("connector must create a dedicated connection")
        if isinstance(max_row_limit, bool) or not isinstance(max_row_limit, int) or not 1 <= max_row_limit <= 100:
            raise ValueError("max_row_limit must be between 1 and 100")
        connection_timeout_seconds = _positive_timeout(
            connect_timeout if connect_timeout is not None else connection_timeout_seconds,
            "connection_timeout_seconds",
        )
        read_timeout_seconds = _positive_timeout(
            read_timeout if read_timeout is not None else read_timeout_seconds,
            "read_timeout_seconds",
        )

        self._profile_source = profile if profile is not None else profile_loader
        self._connections_source = connections_loader
        if connections is not None:
            self._connections_source = lambda: connections
        self._secret_loader = secret_loader
        self._decryptor = _default_decryptor if decryptor is None else decryptor
        self._connector = _legacy_connector if connector is None else connector
        bound_id = None if connection_id is None else _required_text(connection_id, "connection_id")
        if bound_id is None and isinstance(profile, Mapping):
            direct_id = profile.get("connection_id", profile.get("id", ""))
            if str(direct_id or "").strip():
                bound_id = str(direct_id).strip()
        self._bound_connection_id = bound_id
        self._bound_profile_revision = _optional_text(profile_revision)
        self._bound_provider = _optional_text(provider)
        self._bound_mode = _optional_text(mode)
        self._bound_dialect = _optional_text(dialect)
        self._connection_timeout_seconds = connection_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._max_row_limit = int(max_row_limit)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(connection_timeout_seconds={self._connection_timeout_seconds!r}, read_timeout_seconds={self._read_timeout_seconds!r})"

    def profile_snapshot(self, connection_id: str) -> RelationalProfileSnapshot:
        """Resolve one profile's public metadata without decrypting a secret."""

        selected_id = _required_text(connection_id, "connection_id")
        profile = self._load_profile(selected_id)
        normalized, target = _canonical_profile(profile, selected_id)
        del normalized
        return RelationalProfileSnapshot(
            target.connection_id,
            target.profile_revision,
            target.provider,
            target.mode,
            target.dialect,
        )

    def create_factory(
        self,
        connection_id: str | None = None,
        *,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        dialect: str | None = None,
    ) -> ReadOnlyLeaseFactory | LazyRelationalLeaseFactory:
        """Return a lease factory without opening a connection.

        If the complete target snapshot is supplied, the returned object is
        the shared ``ReadOnlyLeaseFactory`` directly.  Otherwise a lazy
        wrapper resolves the selected profile on its first worker call and
        then fixes the target for all subsequent calls.
        """

        selected_id = _required_text(
            self._bound_connection_id if connection_id is None else connection_id,
            "connection_id",
        )
        expected_revision_value = self._bound_profile_revision if profile_revision is None else profile_revision
        expected_revision = _optional_text(expected_revision_value)
        provider = self._bound_provider if provider is None else provider
        mode = self._bound_mode if mode is None else mode
        dialect = self._bound_dialect if dialect is None else dialect
        if expected_revision and dialect and (provider or dialect):
            try:
                target = ReadOnlyTarget(
                    selected_id,
                    expected_revision,
                    canonical_provider(provider or dialect, mode),
                    dialect,
                    mode or "",
                )
            except Exception:
                raise RelationalProfileError(code="UNSUPPORTED_COMBINATION") from None
            return self._build_factory(target)
        return LazyRelationalLeaseFactory(
            self,
            selected_id,
            profile_revision=expected_revision,
            provider=provider,
            mode=mode,
            dialect=dialect,
        )

    def __call__(
        self,
        connection_id: str | None = None,
        *,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        dialect: str | None = None,
        session_id: str | None = None,
        generation: int | None = None,
    ) -> ReadOnlyLeaseFactory | LazyRelationalLeaseFactory | ReadOnlyLease:
        factory = self.create_factory(
            connection_id,
            profile_revision=profile_revision,
            provider=provider,
            mode=mode,
            dialect=dialect,
        )
        if session_id is not None or generation is not None:
            return factory(
                connection_id or self._bound_connection_id,
                session_id=session_id,
                generation=generation,
            )
        return factory

    def connect(
        self,
        connection_id: str | None = None,
        *,
        session_id: str | None = None,
        generation: int | None = None,
        profile_revision: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        dialect: str | None = None,
    ) -> ReadOnlyLease:
        """Open one worker-owned lease through a lazily materialized factory."""

        factory = self.create_factory(
            connection_id,
            profile_revision=profile_revision,
            provider=provider,
            mode=mode,
            dialect=dialect,
        )
        return factory(
            connection_id or self._bound_connection_id,
            session_id=session_id,
            generation=generation,
        )

    def _load_profile(self, connection_id: str, profile_revision: str | None = None) -> Mapping[str, Any]:
        selected_id = _required_text(connection_id, "connection_id")
        source = self._profile_source
        if source is not None:
            if isinstance(source, Mapping):
                return _select_profile(source, selected_id)
            try:
                loaded = _invoke_declared(
                    source,
                    {
                        "connection_id": selected_id,
                        "id": selected_id,
                        "profile_revision": str(profile_revision or ""),
                    },
                    fallback=(selected_id,),
                )
            except Exception:
                raise RelationalProfileError(code="PROFILE_UNAVAILABLE") from None
            return _select_profile(loaded, selected_id)

        source = self._connections_source if self._connections_source is not None else _default_connections_loader
        try:
            loaded = _invoke_declared(
                source,
                {
                    "connection_id": selected_id,
                    "id": selected_id,
                    "profile_revision": str(profile_revision or ""),
                },
                fallback=(selected_id,),
            )
        except Exception:
            raise RelationalProfileError(code="PROFILE_UNAVAILABLE") from None
        return _select_profile(loaded, selected_id)

    def _build_factory(
        self,
        target: ReadOnlyTarget,
        *,
        initial_profile: Mapping[str, Any] | None = None,
    ) -> ReadOnlyLeaseFactory:
        def load_profile(connection_id: str, profile_revision: str = "") -> Mapping[str, Any]:
            if profile_holder[0] is not None:
                value = profile_holder[0]
                profile_holder[0] = None
                return value
            raw = self._load_profile(connection_id, profile_revision or target.profile_revision)
            normalized, _ = _canonical_profile(
                raw,
                connection_id,
                expected_revision=profile_revision or target.profile_revision,
                expected_provider=target.provider,
                expected_mode=target.mode,
                expected_dialect=target.dialect,
            )
            return normalized

        profile_holder: list[Mapping[str, Any] | None] = [initial_profile]

        def load_secret(profile: Mapping[str, Any]) -> Any:
            if self._secret_loader is None:
                return _decrypt_profile_secret(profile, decryptor=self._decryptor)
            try:
                return _invoke_declared(
                    self._secret_loader,
                    {
                        "profile": profile,
                        "connection_id": target.connection_id,
                        "profile_revision": target.profile_revision,
                        "provider": target.provider,
                        "mode": target.mode,
                        "dialect": target.dialect,
                    },
                    fallback=(profile,),
                )
            except Exception:
                raise RelationalSecretError(code="SECRET_UNAVAILABLE") from None

        # These are host-hook values for an explicitly injected connector.
        # The disabled legacy connector never receives them, and no driver
        # attribute is guessed or changed after connection establishment.
        fixed_options = {
            "connect_timeout": self._connection_timeout_seconds,
            "read_timeout": self._read_timeout_seconds,
        }

        def connect(
            provider: str,
            profile: Mapping[str, Any],
            secret: Any,
            **options: Any,
        ) -> Any:
            # ``options`` contains the strategy's fixed read-only flags and
            # the host's fixed timeout hints.  No caller/model argument is
            # accepted to override provider, mode, dialect, or connection id.
            merged = dict(fixed_options)
            supplied = options.get("options")
            if isinstance(supplied, Mapping):
                for key in ("read_only", "autocommit", "autoCommit", "access_mode"):
                    if key in supplied:
                        merged[key] = supplied[key]
            merged.update(fixed_options)
            merged["read_only"] = True
            merged["readonly"] = True
            merged["autocommit"] = False
            merged["autoCommit"] = False
            connector = self._connector
            callable_connector = connector if callable(connector) else getattr(connector, "connect", None)
            if not callable(callable_connector):
                raise RelationalHostError(code="CONNECTOR_UNAVAILABLE")
            try:
                connection = _invoke_declared(
                    callable_connector,
                    {
                        "provider": target.provider,
                        "engine": target.provider,
                        "mode": target.mode,
                        "dialect": target.dialect,
                        "profile": profile,
                        "connection_profile": profile,
                        "secret": secret,
                        "password": secret,
                        "credentials": secret,
                        "connection_id": target.connection_id,
                        "options": merged,
                        **merged,
                    },
                    fallback=(target.provider, profile, secret),
                )
            except ReadOnlyLeaseError:
                raise
            except Exception:
                raise RelationalHostError(code="CONNECT_FAILED") from None
            if connection is None:
                raise RelationalHostError(code="CONNECT_FAILED")
            return connection

        return ReadOnlyLeaseFactory(
            target,
            profile_loader=load_profile,
            secret_loader=load_secret,
            connector=connect,
            max_row_limit=self._max_row_limit,
        )


def _positive_timeout(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be positive") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


__all__ = [
    "LazyRelationalLeaseFactory",
    "RelationalHostError",
    "RelationalProfileError",
    "RelationalProfileSnapshot",
    "RelationalReadOnlyHost",
    "RelationalScopeError",
    "RelationalSecretError",
    "compute_profile_revision",
]
