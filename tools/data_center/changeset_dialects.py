"""Bound SQL dialect policies for the relational ChangeSet layer.

These policies only construct SQL and typed bind containers. They do not load
profiles, inspect drivers, open connections, or perform database I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import Any

from tools.data_center.changeset import BoundParameters, ChangeSetError, _validate_identifier
from tools.data_center.manual_session import ManualSessionTarget
from tools.data_center.profile_host import ConnectionChoice


_MYSQL_PAIRS = frozenset({("mysql", "mysql", "mysql"), ("oceanbase_mysql", "mysql", "mysql")})
_ORACLE_PAIRS = frozenset({("oracle", "oracle", "oracle"), ("oceanbase_oracle", "oracle", "oracle")})


def _validate_binding(choice: ConnectionChoice, target: ManualSessionTarget) -> tuple[str, str]:
    if not isinstance(choice, ConnectionChoice) or not isinstance(target, ManualSessionTarget):
        raise ChangeSetError("DIALECT_TARGET_REQUIRED")
    if (
        choice.connection_id != target.connection_id
        or choice.revision != target.profile_revision
        or choice.database != target.database
    ):
        raise ChangeSetError("DIALECT_TARGET_MISMATCH")
    return choice.provider.casefold(), choice.mode.casefold()


@dataclass(frozen=True, slots=True, init=False)
class MySqlChangeSetDialect:
    """PyMySQL-compatible backtick, percent-template, positional policy."""

    choice: ConnectionChoice
    target: ManualSessionTarget

    def __init__(self, choice: ConnectionChoice, target: ManualSessionTarget) -> None:
        provider, mode = _validate_binding(choice, target)
        if (provider, mode, choice.dialect.casefold()) not in _MYSQL_PAIRS:
            raise ChangeSetError("UNSUPPORTED_SQL_DIALECT")
        if not (target.database or target.schema):
            raise ChangeSetError("MYSQL_DATABASE_REQUIRED")
        if target.schema and target.database and target.schema != target.database:
            raise ChangeSetError("MYSQL_SCHEMA_DATABASE_MISMATCH")
        object.__setattr__(self, "choice", choice)
        object.__setattr__(self, "target", target)

    def quote_identifier(self, identifier: str) -> str:
        name = _validate_identifier(identifier)
        # PyMySQL Cursor.mogrify() applies Python %-formatting to the complete
        # query template. Double every literal percent in identifiers so
        # metadata such as "rate%s" cannot become a bind placeholder.
        return "`" + name.replace("`", "``").replace("%", "%%") + "`"

    def qualified_table(self, target: ManualSessionTarget, table: str) -> str:
        _validate_policy_target(self.target, target)
        name = _validate_identifier(table)
        database = target.database or target.schema
        if not database:
            raise ChangeSetError("MYSQL_DATABASE_REQUIRED")
        parts = ([database] if database else []) + [name]
        return ".".join(self.quote_identifier(part) for part in parts)

    def bind_values(self, values: Sequence[Any]) -> BoundParameters:
        parameters = tuple(values)
        return BoundParameters(tuple("%s" for _ in parameters), parameters)


@dataclass(frozen=True, slots=True, init=False)
class OracleChangeSetDialect:
    """Oracle bind policy using the syntax required by each direct driver."""

    choice: ConnectionChoice
    target: ManualSessionTarget
    _odbc_numeric_binds: bool

    def __init__(self, choice: ConnectionChoice, target: ManualSessionTarget) -> None:
        provider, mode = _validate_binding(choice, target)
        if (provider, mode, choice.dialect.casefold()) not in _ORACLE_PAIRS:
            raise ChangeSetError("UNSUPPORTED_SQL_DIALECT")
        object.__setattr__(self, "choice", choice)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "_odbc_numeric_binds", provider == "oceanbase_oracle")

    def quote_identifier(self, identifier: str) -> str:
        name = _validate_identifier(identifier)
        if self._odbc_numeric_binds and re.search(r":[1-9]\d*\b", name):
            # The existing OB Oracle ODBC cursor translates :1/:2 markers
            # outside quoted SQL strings/identifiers. Reject this rare name
            # conservatively instead of depending on its lexer for collisions.
            raise ChangeSetError("ODBC_IDENTIFIER_BIND_COLLISION")
        return '"' + name.replace('"', '""') + '"'

    def qualified_table(self, target: ManualSessionTarget, table: str) -> str:
        _validate_policy_target(self.target, target)
        name = _validate_identifier(table)
        # In Oracle, target.database is the service/database selected at login,
        # never the owner's schema. A missing captured schema stays unqualified.
        parts = ([target.schema] if target.schema else []) + [name]
        return ".".join(self.quote_identifier(part) for part in parts)

    def bind_values(self, values: Sequence[Any]) -> BoundParameters:
        if self._odbc_numeric_binds:
            parameters = tuple(values)
            return BoundParameters(tuple(f":{index}" for index in range(1, len(parameters) + 1)), parameters)
        parameters = {f"p{index}": value for index, value in enumerate(values)}
        markers = tuple(f":p{index}" for index in range(len(parameters)))
        return BoundParameters(markers, parameters)


def _validate_policy_target(
    bound_target: ManualSessionTarget,
    supplied_target: ManualSessionTarget,
) -> None:
    if not isinstance(supplied_target, ManualSessionTarget) or supplied_target != bound_target:
        raise ChangeSetError("DIALECT_TARGET_MISMATCH")


def create_changeset_dialect(
    choice: ConnectionChoice,
    target: ManualSessionTarget,
) -> MySqlChangeSetDialect | OracleChangeSetDialect:
    """Create a no-I/O dialect bound to the exact provider and tab target."""

    provider, mode = _validate_binding(choice, target)
    pair = (provider, mode, choice.dialect.casefold())
    if pair in _MYSQL_PAIRS:
        return MySqlChangeSetDialect(choice, target)
    if pair in _ORACLE_PAIRS:
        return OracleChangeSetDialect(choice, target)
    raise ChangeSetError("UNSUPPORTED_SQL_DIALECT")


__all__ = [
    "MySqlChangeSetDialect",
    "OracleChangeSetDialect",
    "create_changeset_dialect",
]
