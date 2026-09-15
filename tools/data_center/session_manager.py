"""Worker-owned read-only database session bookkeeping for DC-02.

The manager records session scope and generation only.  A connection object is
created by the query worker and is closed by that same worker; no driver or
credential is retained here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import threading
from typing import Any


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


@dataclass(frozen=True, slots=True)
class Session:
    """Immutable scope handed to a query worker.

    ``driver_factory`` is deliberately excluded from the public dictionary and
    representation.  It is an internal callable, never a connection object.
    """

    session_id: str
    connection_id: str
    generation: int
    dialect: str = ""
    driver_factory: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _required_text(self.session_id, "session_id"))
        object.__setattr__(self, "connection_id", _required_text(self.connection_id, "connection_id"))
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        object.__setattr__(self, "dialect", str(self.dialect or "").strip().lower())

    @property
    def id(self) -> str:
        return self.session_id

    @property
    def connection(self) -> str:
        return self.connection_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "connection_id": self.connection_id,
            "generation": self.generation,
            "dialect": self.dialect,
        }


# These names appeared in early DC-02 notes and are useful to hosts that call
# the immutable scope a lease rather than a session.
SessionLease = Session
SessionContext = Session


@dataclass(slots=True)
class _SessionEntry:
    session: Session
    closed: bool = False


class SessionManager:
    """Track immutable query-session scope without owning database objects.

    A manager may be constructed with one connection factory or with a mapping
    from connection id to factories.  Neither form is invoked by
    :meth:`create_session`; the query worker resolves and invokes the factory
    after it starts, which keeps driver thread affinity explicit.
    """

    def __init__(self, driver_factory=None, *, connect=None, start_generation: int = 0, **_kwargs):
        if driver_factory is not None and connect is not None:
            raise TypeError("provide driver_factory or connect, not both")
        if isinstance(start_generation, bool) or not isinstance(start_generation, int) or start_generation < 0:
            raise ValueError("start_generation must be a non-negative integer")
        self._driver_factory = driver_factory if driver_factory is not None else connect
        self._start_generation = int(start_generation)
        self._sessions: dict[str, _SessionEntry] = {}
        self._connection_generations: dict[str, int] = {}
        self._session_generations: dict[str, int] = {}
        self._next_session_number = 0
        self._lock = threading.RLock()

    def create_session(
        self,
        connection_id: str,
        driver_factory: Any = None,
        *,
        session_id: str | None = None,
        generation: int | None = None,
        dialect: str = "",
        connect: Any = None,
    ) -> Session:
        """Register a session and return its fixed scope.

        ``driver_factory`` is stored only as an opaque worker dependency.  It
        is never called on this thread.  Reusing a session id advances its
        generation, so an old query cannot write into the new target.
        """

        connection = _required_text(connection_id, "connection_id")
        if driver_factory is not None and connect is not None:
            raise TypeError("provide driver_factory or connect, not both")
        factory = driver_factory if driver_factory is not None else connect
        with self._lock:
            if session_id is None:
                self._next_session_number += 1
                sid = f"session-{self._next_session_number}"
            else:
                sid = _required_text(session_id, "session_id")

            previous = self._sessions.get(sid)
            if previous is not None and not previous.closed:
                raise ValueError(f"session is already open: {sid}")

            previous_generation = max(
                self._connection_generations.get(connection, self._start_generation),
                self._session_generations.get(sid, self._start_generation),
            )
            if generation is None:
                actual_generation = previous_generation + 1
            else:
                if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
                    raise ValueError("generation must be a positive integer")
                if generation <= previous_generation:
                    raise ValueError("generation must advance for a connection")
                actual_generation = generation
            self._connection_generations[connection] = actual_generation
            self._session_generations[sid] = actual_generation

            if factory is None:
                factory = self._resolve_factory_locked(connection)
            session = Session(
                session_id=sid,
                connection_id=connection,
                generation=actual_generation,
                dialect=dialect,
                driver_factory=factory,
            )
            self._sessions[sid] = _SessionEntry(session=session)
            return session

    # Common host spellings.
    open_session = create_session
    open = create_session
    create = create_session

    def get_session(self, session_id: str) -> Session | None:
        sid = str(session_id or "").strip()
        with self._lock:
            entry = self._sessions.get(sid)
            return None if entry is None or entry.closed else entry.session

    session = get_session

    def is_current(
        self,
        session_id: str,
        generation: int,
        connection_id: str | None = None,
    ) -> bool:
        """Return whether a query still belongs to the open target."""

        sid = str(session_id or "").strip()
        with self._lock:
            entry = self._sessions.get(sid)
            if entry is None or entry.closed:
                return False
            target = entry.session
            if isinstance(generation, bool) or not isinstance(generation, int):
                return False
            if target.generation != generation:
                return False
            return connection_id is None or target.connection_id == str(connection_id).strip()

    matches = is_current
    accepts = is_current

    def close_session(self, session_id: str, generation: int | None = None) -> bool:
        """Close the logical lease; an active worker still owns its driver.

        This only invalidates future result delivery.  The worker closes its
        own driver in its ``finally`` block after the driver call returns.
        """

        sid = str(session_id or "").strip()
        with self._lock:
            entry = self._sessions.get(sid)
            if entry is None or entry.closed:
                return False
            if generation is not None and entry.session.generation != generation:
                return False
            entry.closed = True
            return True

    close = close_session
    invalidate = close_session
    invalidate_session = close_session

    def replace_session(
        self,
        session_id: str,
        connection_id: str,
        driver_factory: Any = None,
        *,
        dialect: str = "",
        connect: Any = None,
    ) -> Session:
        """Invalidate an old id and allocate a strictly newer generation."""

        sid = _required_text(session_id, "session_id")
        with self._lock:
            old = self._sessions.get(sid)
            if old is not None and not old.closed:
                old.closed = True
            previous = self._connection_generations.get(str(connection_id).strip(), self._start_generation)
            return self.create_session(
                connection_id,
                driver_factory,
                session_id=sid,
                generation=previous + 1,
                dialect=dialect,
                connect=connect,
            )

    def close_all(self) -> int:
        with self._lock:
            count = 0
            for entry in self._sessions.values():
                if not entry.closed:
                    entry.closed = True
                    count += 1
            return count

    def snapshot(self) -> tuple[Session, ...]:
        with self._lock:
            return tuple(entry.session for entry in self._sessions.values() if not entry.closed)

    def _factory_for(self, session: Session) -> Any:
        """Return the opaque factory for worker-side invocation."""

        if session.driver_factory is not None:
            return session.driver_factory
        with self._lock:
            return self._resolve_factory_locked(session.connection_id)

    def _resolve_factory_locked(self, connection_id: str) -> Any:
        source = self._driver_factory
        if isinstance(source, Mapping):
            return source.get(connection_id)
        return source


__all__ = ["Session", "SessionContext", "SessionLease", "SessionManager"]
