"""Provider-aware factories for manual relational tab sessions.

This module binds the existing target-pinned DBAPI driver to one manual tab.
It deliberately does not own profiles, credentials, or a driver lease: profile
resolution, secret resolution, connection creation, and every DBAPI call happen
inside :class:`ManualSession`'s persistent owner thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from tools.data_center.manual_driver import (
    ManualDriverError,
    RelationalManualDriverFactory,
    classify_relational_statement,
)
from tools.data_center.manual_session import (
    ManualSession,
    ManualSessionTarget,
    StatementClassification,
    StatementKind,
    TransactionEffect,
)
from tools.data_center.object_tree import ObjectTreeError, TreeNode, TreeTarget, VerifiedTableRef
from tools.data_center.profile_host import ConnectionChoice, DataCenterProfileHost


class _TableScopeResolver(Protocol):
    def resolve_table_source(
        self,
        target: TreeTarget,
        node: TreeNode,
        *,
        generation: int,
    ) -> VerifiedTableRef: ...


@dataclass(frozen=True, slots=True)
class BoundManualTableScope:
    """Immutable binding between one manual tab and one live signed tree node."""

    service: _TableScopeResolver
    tree_target: TreeTarget
    node: TreeNode
    tree_generation: int
    manual_target: ManualSessionTarget
    choice: ConnectionChoice
    verified_ref: VerifiedTableRef

    @classmethod
    def capture(
        cls,
        service: _TableScopeResolver,
        tree_target: TreeTarget,
        node: TreeNode,
        tree_generation: int,
        manual_target: ManualSessionTarget,
        choice: ConnectionChoice,
    ) -> "BoundManualTableScope":
        """Resolve an existing registry entry without starting a DB operation."""

        if (
            not callable(getattr(service, "resolve_table_source", None))
            or not isinstance(tree_target, TreeTarget)
            or not isinstance(node, TreeNode)
            or not isinstance(manual_target, ManualSessionTarget)
            or not isinstance(choice, ConnectionChoice)
            or isinstance(tree_generation, bool)
            or not isinstance(tree_generation, int)
            or tree_generation < 0
        ):
            raise ManualDriverError("TABLE_SCOPE_INVALID")
        ref = cls._resolve(service, tree_target, node, tree_generation)
        cls._validate_binding(ref, tree_target, manual_target, choice)
        return cls(service, tree_target, node, tree_generation, manual_target, choice, ref)

    def validate_current(
        self,
        target: ManualSessionTarget,
        choice: ConnectionChoice,
    ) -> VerifiedTableRef:
        """Recheck the live node and complete tab/choice identity in place."""

        if target != self.manual_target or choice != self.choice:
            raise ManualDriverError("TABLE_SCOPE_MISMATCH")
        ref = self._resolve(self.service, self.tree_target, self.node, self.tree_generation)
        if ref != self.verified_ref:
            raise ManualDriverError("TABLE_SCOPE_STALE")
        self._validate_binding(ref, self.tree_target, target, choice)
        return ref

    @staticmethod
    def _resolve(
        service: _TableScopeResolver,
        tree_target: TreeTarget,
        node: TreeNode,
        generation: int,
    ) -> VerifiedTableRef:
        try:
            ref = service.resolve_table_source(tree_target, node, generation=generation)
        except ObjectTreeError as error:
            code = "TABLE_SCOPE_STALE" if error.code == "STALE_NODE" else "TABLE_SCOPE_INVALID"
            raise ManualDriverError(code) from None
        except Exception:
            raise ManualDriverError("TABLE_SCOPE_INVALID") from None
        if not isinstance(ref, VerifiedTableRef):
            raise ManualDriverError("TABLE_SCOPE_INVALID")
        return ref

    @staticmethod
    def _validate_binding(
        ref: VerifiedTableRef,
        tree_target: TreeTarget,
        target: ManualSessionTarget,
        choice: ConnectionChoice,
    ) -> None:
        from tools.data_center.table_source import TableSourceError, validate_table_target

        try:
            validate_table_target(ref, tree_target, target, choice)
        except TableSourceError:
            raise ManualDriverError("TABLE_SCOPE_MISMATCH") from None
        if (
            choice.provider.casefold() in {"oracle", "oceanbase_oracle"}
            and choice.schema_allowlist
            and ref.schema not in choice.schema_allowlist
        ):
            raise ManualDriverError("TABLE_SCOPE_MISMATCH")


@dataclass(frozen=True, slots=True)
class ManualProviderCapability:
    """Verified adapter contract for a selected provider/mode pair."""

    provider: str
    mode: str
    supported: bool
    reason_code: str
    supports_autocommit_control: bool
    supports_savepoints: bool
    savepoint_release: str
    supports_native_cancel: bool
    timeout_scope: str


_SUPPORTED_PAIRS = {
    ("mysql", "mysql"): ("mysql", "sql"),
    ("oracle", "oracle"): ("oracle", "implicit-on-commit"),
    ("oceanbase_mysql", "mysql"): ("mysql", "sql"),
    ("oceanbase_oracle", "oracle"): ("oracle", "implicit-on-commit"),
}


def inspect_manual_provider(choice: ConnectionChoice) -> ManualProviderCapability:
    """Return a no-I/O capability decision for an immutable host choice."""

    if not isinstance(choice, ConnectionChoice):
        raise TypeError("choice must be ConnectionChoice")
    provider = choice.provider.casefold()
    mode = choice.mode.casefold()
    if provider == "dameng":
        return ManualProviderCapability(
            provider, mode, False, "MANUAL_PROVIDER_UNSUPPORTED",
            False, False, "unsupported", False, "unsupported",
        )
    expected = _SUPPORTED_PAIRS.get((provider, mode))
    if expected is None or choice.dialect.casefold() != expected[0]:
        return ManualProviderCapability(
            provider, mode, False, "MANUAL_PROVIDER_UNSUPPORTED",
            False, False, "unsupported", False, "unsupported",
        )
    _, release = expected
    return ManualProviderCapability(
        provider=provider,
        mode=mode,
        supported=True,
        reason_code="",
        supports_autocommit_control=True,
        supports_savepoints=True,
        savepoint_release=release,
        supports_native_cancel=False,
        timeout_scope="connect-and-per-call",
    )


def classify_manual_sql(choice: ConnectionChoice, sql: str) -> StatementClassification:
    """Classify SQL using the selected backend's transaction semantics.

    Unsupported providers remain usable for capability reporting but do not
    acquire a guessed classifier.  Unknown syntax from supported providers is
    returned as UNKNOWN by the existing vendor-aware classifier.
    """

    capability = inspect_manual_provider(choice)
    if not capability.supported:
        return StatementClassification(StatementKind.OTHER, TransactionEffect.UNKNOWN)
    return classify_relational_statement(sql, capability.provider)


class ManualRelationalProviderFactory:
    """Lazy, target-bound adapter factory intended for one manual tab only."""

    def __init__(
        self,
        choice: ConnectionChoice,
        *,
        profile_host: DataCenterProfileHost | None = None,
        connector: Callable[[Mapping[str, Any], Any], Any] | None = None,
        secret_resolver: Callable[[Any], Any] | None = None,
        connect_timeout: float = 8.0,
        read_timeout: float = 15.0,
        table_scope: BoundManualTableScope | None = None,
    ) -> None:
        self.choice = choice
        self.capability = inspect_manual_provider(choice)
        if table_scope is not None:
            if not isinstance(table_scope, BoundManualTableScope) or table_scope.choice != choice:
                raise ManualDriverError("TABLE_SCOPE_MISMATCH")
            table_scope.validate_current(table_scope.manual_target, choice)
        self.table_scope = table_scope
        self._delegate = None
        if self.capability.supported:
            self._delegate = RelationalManualDriverFactory(
                choice,
                profile_host=profile_host,
                connector=connector,
                secret_resolver=secret_resolver,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                table_scope=table_scope,
            )

    def __call__(self, target: ManualSessionTarget):
        if not self.capability.supported or self._delegate is None:
            raise ManualDriverError(self.capability.reason_code or "MANUAL_PROVIDER_UNSUPPORTED")
        if (
            target.connection_id != self.choice.connection_id
            or target.profile_revision != self.choice.revision
            or target.database != self.choice.database
        ):
            raise ManualDriverError("PROFILE_MISMATCH")
        if self.table_scope is not None and target != self.table_scope.manual_target:
            raise ManualDriverError("TABLE_SCOPE_MISMATCH")
        # The delegate re-loads the exact profile revision and validates its
        # fixed database/schema and live table scope before it resolves the
        # secret or calls the connector.
        return self._delegate(target)


def create_manual_session(
    target: ManualSessionTarget,
    choice: ConnectionChoice,
    *,
    table_scope: BoundManualTableScope | None = None,
    **factory_options: Any,
) -> ManualSession:
    """Create a lazy session bound to exactly one tab and provider choice."""

    factory = ManualRelationalProviderFactory(choice, table_scope=table_scope, **factory_options)
    return ManualSession(
        target,
        factory,
        lambda sql: classify_manual_sql(choice, sql),
    )


__all__ = [
    "BoundManualTableScope",
    "ManualProviderCapability",
    "ManualRelationalProviderFactory",
    "classify_manual_sql",
    "create_manual_session",
    "inspect_manual_provider",
]
