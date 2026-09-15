"""Fail-closed read-only SQL policy boundary for the data-center Agent."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import sqlglot
from sqlglot import ErrorLevel, exp
from sqlglot.errors import SqlglotError


POLICY_VERSION = "dc-02"

_DIALECT_MAP: dict[str, tuple[str, bool]] = {
    "mysql": ("mysql", False),
    "oceanbase_mysql": ("mysql", False),
    "oracle": ("oracle", False),
    "oceanbase_oracle": ("oracle", False),
    # Dameng is parsed as Oracle SQL, but remains a restricted compatibility mode.
    "dameng": ("oracle", True),
}

_BLOCKED_FUNCTIONS = frozenset(
    {
        "SLEEP",
        "BENCHMARK",
        "GET_LOCK",
        "RELEASE_LOCK",
        "RELEASE_ALL_LOCKS",
        "LOAD_FILE",
        "READ_CSV",
        "READ_PARQUET",
        "READ_JSON",
        "READ_XML",
        "OPENROWSET",
        "XP_CMDSHELL",
        "LAST_INSERT_ID",
        "PG_SLEEP",
    }
)
_BLOCKED_QUALIFIERS = frozenset(
    {
        "UTL_HTTP",
        "UTL_FILE",
        "UTL_TCP",
        "UTL_SMTP",
        "UTL_INADDR",
        "DBMS_LOCK",
        "DBMS_PIPE",
        "DBMS_SCHEDULER",
        "DBMS_JOB",
        "DBMS_ALERT",
        "DBMS_AQ",
    }
)
_BLOCKED_QUALIFIER_PREFIXES = ("UTL_", "DBMS_")
# SQLGlot represents functions it does not know as ``exp.Anonymous``.  Keep
# that extension point closed: a stored function/UDF may perform writes,
# external I/O, or session mutation even when its outer statement is SELECT.
# These two MySQL introspection functions are explicitly treated as pure reads
# for the supported Agent surface.
_ALLOWED_ANONYMOUS_FUNCTIONS = frozenset({"CONNECTION_ID", "IS_USED_LOCK"})
_EXECUTABLE_COMMENT_START = re.compile(r"/\*\s*(?:!|m!)", re.IGNORECASE)
_BLOCKED_PACKAGE_NAME = re.compile(r"(?:UTL_|DBMS_)[A-Za-z0-9_$#]*", re.IGNORECASE)

_BLOCKED_NODE_TYPES = tuple(
    node_type
    for node_name in (
        "Command",
        "Transaction",
        "Set",
        "Use",
        "Grant",
        "Revoke",
        "ReadCSV",
        "ReadParquet",
        "ReadJSON",
        "ReadXML",
        "External",
        "LockingStatement",
    )
    if isinstance((node_type := getattr(exp, node_name, None)), type)
)


@dataclass(frozen=True, slots=True)
class SQLDecision:
    """Structured result returned by SQL validation."""

    allowed: bool
    code: str = "OK"
    message: str = ""
    dialect: str = ""
    sql: str = ""
    tables: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.allowed

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "ok": self.allowed,
            "code": self.code,
            "message": self.message,
            "dialect": self.dialect,
            "sql": self.sql,
            "tables": list(self.tables),
            "evidence": list(self.evidence),
            "limits": {"policy_version": POLICY_VERSION},
        }


class SQLPolicy:
    """Allow exactly one parsed, read-only ``SELECT`` statement."""

    version = POLICY_VERSION

    def validate(self, sql: str, dialect: str = "oracle") -> SQLDecision:
        if not isinstance(sql, str) or not sql.strip():
            return SQLDecision(
                False,
                "ARGUMENT_INVALID",
                "SQL 不能为空",
                dialect=str(dialect),
                sql="",
                evidence=("argument=sql",),
            )

        normalized_sql = sql.strip()
        if _contains_executable_comment(normalized_sql):
            return SQLDecision(
                False,
                "POLICY_DENIED",
                "SQL 包含可执行注释",
                dialect=str(dialect).strip().lower(),
                sql=normalized_sql,
                evidence=("sql_comment=executable",),
            )
        if _contains_blocked_package_reference(normalized_sql):
            return SQLDecision(
                False,
                "POLICY_DENIED",
                "SQL 包含不允许的外部或会话包调用",
                dialect=str(dialect).strip().lower(),
                sql=normalized_sql,
                evidence=("package_namespace=blocked",),
            )
        if not isinstance(dialect, str) or not dialect.strip():
            return SQLDecision(
                False,
                "ARGUMENT_INVALID",
                "SQL 方言无效",
                dialect=str(dialect),
                sql=normalized_sql,
                evidence=("argument=dialect",),
            )

        requested_dialect = dialect.strip().lower()
        mapped_dialect = _DIALECT_MAP.get(requested_dialect)
        if mapped_dialect is None:
            return SQLDecision(
                False,
                "DIALECT_UNSUPPORTED",
                "SQL 方言不受支持",
                dialect=requested_dialect,
                sql=normalized_sql,
                evidence=(f"dialect={requested_dialect}", "dialect_supported=false"),
            )

        normalized_dialect, restricted = mapped_dialect
        base_evidence = [
            f"dialect={requested_dialect}",
            f"parser_dialect={normalized_dialect}",
        ]
        if restricted:
            base_evidence.append("dialect_restricted=true")

        try:
            statements = sqlglot.parse(
                normalized_sql,
                read=normalized_dialect,
                error_level=ErrorLevel.RAISE,
            )
        except (SqlglotError, ValueError, TypeError):
            return SQLDecision(
                False,
                "SQL_INVALID",
                "SQL 解析失败",
                dialect=normalized_dialect,
                sql=normalized_sql,
                evidence=tuple(base_evidence + ["parser=sqlglot", "parse_success=false"]),
            )

        if len(statements) != 1 or statements[0] is None:
            return SQLDecision(
                False,
                "MULTI_STATEMENT",
                "只允许执行一条 SQL 语句",
                dialect=normalized_dialect,
                sql=normalized_sql,
                evidence=tuple(base_evidence + [f"statement_count={len(statements)}"]),
            )

        statement = statements[0]
        tables = _collect_tables(statement)
        statement_evidence = base_evidence + ["statement_count=1", f"root={type(statement).__name__}"]
        if tables:
            statement_evidence.extend(f"table={table}" for table in tables)

        if not isinstance(statement, exp.Select):
            return SQLDecision(
                False,
                "POLICY_DENIED",
                "只允许只读 SELECT 语句",
                dialect=normalized_dialect,
                sql=normalized_sql,
                tables=tables,
                evidence=tuple(statement_evidence + ["policy=select_only"]),
            )

        blocked_reason = _blocked_reason(statement)
        if blocked_reason is not None:
            return SQLDecision(
                False,
                "POLICY_DENIED",
                "SELECT 包含不允许的副作用、阻塞或外部访问操作",
                dialect=normalized_dialect,
                sql=normalized_sql,
                tables=tables,
                evidence=tuple(statement_evidence + [blocked_reason]),
            )

        return SQLDecision(
            True,
            dialect=normalized_dialect,
            sql=normalized_sql,
            tables=tables,
            evidence=tuple(statement_evidence + ["policy=read_only_select"]),
        )

    decide = validate
    check = validate
    __call__ = validate


def validate_sql(sql: str, dialect: str = "oracle") -> SQLDecision:
    return SQLPolicy().validate(sql, dialect)


def _collect_tables(statement: Any) -> tuple[str, ...]:
    """Return unique table names found in a parsed statement for audit output."""

    names: list[str] = []
    for table in statement.find_all(exp.Table):
        name = str(table.name).strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return str(function.name).strip().upper()
    return str(function.sql_name()).strip().upper()


def _expression_path(node: Any) -> str:
    if isinstance(node, exp.Identifier):
        return str(node.name).strip()
    if isinstance(node, exp.Table):
        return str(node.name).strip()
    if isinstance(node, exp.Dot):
        left = _expression_path(node.this)
        right = _expression_path(node.expression)
        return ".".join(part for part in (left, right) if part)
    if isinstance(node, exp.Func):
        return _function_name(node)
    return ""


def _is_variable_assignment(node: Any) -> bool:
    if isinstance(node, exp.PropertyEQ):
        return True
    if isinstance(node, exp.EQ):
        target = node.this
        return isinstance(target, exp.Parameter) and target.find(exp.Var) is not None
    return False


def _blocked_reason(statement: Any) -> str | None:
    for node in statement.walk():
        if isinstance(node, (exp.DML, exp.DDL)):
            return f"node={type(node).__name__}"
        if _BLOCKED_NODE_TYPES and isinstance(node, _BLOCKED_NODE_TYPES):
            return f"node={type(node).__name__}"
        if isinstance(node, exp.Lock):
            return "node=Lock"
        if isinstance(node, exp.Into):
            return "node=Into"
        if _is_variable_assignment(node):
            return "node=MySQLVariableAssignment"
        if isinstance(node, exp.Column) and node.name.upper() == "NEXTVAL":
            return "node=NEXTVAL"
        if isinstance(node, exp.Dot):
            path = _expression_path(node)
            parts = tuple(part.upper() for part in path.split(".") if part)
            if any(part in _BLOCKED_QUALIFIERS for part in parts):
                return f"function={path.upper()}"
            # Oracle package functions are an open-ended extension point.  A
            # finite deny-list is unsafe because packages such as DBMS_XMLGEN
            # and DBMS_APPLICATION_INFO can perform external I/O or mutate
            # session state without appearing in the list above.  Reject the
            # whole UTL_*/DBMS_* package namespace at the AST boundary.
            if any(part.startswith(_BLOCKED_QUALIFIER_PREFIXES) for part in parts):
                return f"function={path.upper()}"
            if isinstance(node.expression, exp.Func):
                # A qualified function is a stored function/package call.  It
                # is not possible to establish that it is side-effect free
                # from the SQL text alone, so reject it even when its package
                # name is not one of the known dangerous namespaces.
                return f"node=QualifiedFunction:{path.upper()}"
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if (
                name in _BLOCKED_FUNCTIONS
                or name in _BLOCKED_QUALIFIERS
                or name.startswith(_BLOCKED_QUALIFIER_PREFIXES)
            ):
                return f"function={name}"
            if isinstance(node, exp.Anonymous) and name not in _ALLOWED_ANONYMOUS_FUNCTIONS:
                return f"function=UNKNOWN:{name}"
    return None


def _contains_executable_comment(sql: str) -> bool:
    """Return whether SQL contains a MySQL/MariaDB executable comment.

    A plain substring check would reject a legitimate string containing
    ``/*!``.  This small scanner only recognizes the marker outside quoted
    literals/identifiers while still treating the comment itself as an
    executable boundary.
    """

    quote: str | None = None
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if quote is not None:
            if char == "\\" and quote in {"'", '"', "`"}:
                index += 2
                continue
            if char == quote:
                if index + 1 < length and sql[index + 1] == quote and quote in {"'", '"'}:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if sql.startswith("--", index) or char == "#":
            newline = sql.find("\n", index + (2 if sql.startswith("--", index) else 1))
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            if _EXECUTABLE_COMMENT_START.match(sql, index):
                return True
            end = sql.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        index += 1
    return False


def _contains_blocked_package_reference(sql: str) -> bool:
    """Return whether SQL references an Oracle UTL_/DBMS_ package.

    Some parser dialects rewrite an unparenthesized package member such as
    ``DBMS_RANDOM.VALUE`` into a different built-in node.  Keep this namespace
    check lexical and outside quoted literals/comments so that parser rewrites
    cannot turn a package call into an apparently harmless expression.
    """

    quote: str | None = None
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if quote is not None:
            if char == "\\" and quote in {"'", '`'}:
                index += 2
                continue
            if char == quote:
                if index + 1 < length and sql[index + 1] == quote and quote == "'":
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {'"', '`', '['}:
            closing = ']' if char == '[' else char
            end = sql.find(closing, index + 1)
            if end >= 0:
                quoted_name = sql[index + 1 : end].strip()
                if quoted_name.upper().startswith(_BLOCKED_QUALIFIER_PREFIXES):
                    if _skip_sql_space_and_comments(sql, end + 1) < length and sql[_skip_sql_space_and_comments(sql, end + 1)] == ".":
                        return True
            if char in {'"', '`'}:
                quote = char
            index += 1
            continue
        if char in {"'", '`'}:
            quote = char
            index += 1
            continue
        if sql.startswith("--", index) or char == "#":
            newline = sql.find("\n", index + (2 if sql.startswith("--", index) else 1))
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        package = _BLOCKED_PACKAGE_NAME.match(sql, index)
        if package and (index == 0 or sql[index - 1] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$#"):
            after_name = _skip_sql_space_and_comments(sql, package.end())
            if after_name < length and sql[after_name] == ".":
                return True
        index += 1
    return False


def _skip_sql_space_and_comments(sql: str, index: int) -> int:
    """Skip whitespace and comments while looking for a package member dot."""

    length = len(sql)
    while index < length:
        while index < length and sql[index].isspace():
            index += 1
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                return length
            index = end + 2
            continue
        if sql.startswith("--", index) or sql[index : index + 1] == "#":
            newline = sql.find("\n", index + (2 if sql.startswith("--", index) else 1))
            if newline < 0:
                return length
            index = newline + 1
            continue
        break
    return index


__all__ = ["POLICY_VERSION", "SQLDecision", "SQLPolicy", "validate_sql"]
