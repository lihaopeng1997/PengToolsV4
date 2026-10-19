"""Owner-thread catalog verification and bounded relational table reads.

Only a live ObjectTreeService node reference can supply the table identity.
Catalog values are always bound parameters; identifiers in the eventual page
query come only from verified catalog rows and pass through the selected
provider's identifier policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import math
from threading import Event
from typing import Any, Protocol

from .changeset import ChangeSetError, SqlDialectPolicy, VerifiedColumn, VerifiedTableMetadata
from .manual_session import ManualQueryPage, ManualSessionTarget
from .object_tree import TreeTarget, VerifiedTableRef
from .profile_host import ConnectionChoice


MAX_TABLE_COLUMNS = 1024
_UNSUPPORTED_COMPARE_TYPES = frozenset(
    {
        "blob", "tinyblob", "mediumblob", "longblob",
        "text", "tinytext", "mediumtext", "longtext",
        "clob", "nclob", "bfile", "long", "long raw", "xmltype", "anydata",
    }
)
_UNSUPPORTED_EDIT_TYPES = _UNSUPPORTED_COMPARE_TYPES | frozenset(
    {"geometry", "point", "linestring", "polygon", "multipoint", "multilinestring", "multipolygon", "geometrycollection"}
)


class TableSourceError(RuntimeError):
    """Stable, non-sensitive error raised by table metadata and SQL builders."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "TABLE_SOURCE_FAILED")
        super().__init__(self.code)


class TableSourceDriver(Protocol):
    def execute(self, sql: str, parameters: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class TableColumnMetadata:
    name: str
    type_name: str
    nullable: bool
    has_default: bool | None
    generated: bool | None
    editable: bool
    comparable: bool
    required_on_insert: bool
    data_length: int | None = None
    data_precision: int | None = None
    data_scale: int | None = None


@dataclass(frozen=True, slots=True)
class TableSourceMetadata:
    target: ManualSessionTarget
    signed_ref: VerifiedTableRef
    columns: tuple[TableColumnMetadata, ...]
    primary_key: tuple[str, ...]
    is_base_table: bool
    verified_changeset: VerifiedTableMetadata | None
    read_only_reason: str

    @property
    def editable(self) -> bool:
        return self.verified_changeset is not None


@dataclass(frozen=True, slots=True)
class TableFilter:
    column: str
    operator: str
    value: Any = None


@dataclass(frozen=True, slots=True)
class TableOrder:
    column: str
    descending: bool = False


FILTER_OPERATORS = ("=", "<>", ">", ">=", "<", "<=", "contains", "starts_with", "IS NULL", "IS NOT NULL")
MAX_TABLE_FILTERS = 32
MAX_TABLE_ORDERS = 8


@dataclass(frozen=True, slots=True)
class TableQuerySpec:
    filters: tuple[TableFilter, ...] = ()
    orders: tuple[TableOrder, ...] = ()


def validate_table_query(query: Any) -> TableQuerySpec:
    """Copy only immutable, bounded query inputs before crossing to the owner."""
    if type(query) is not TableQuerySpec or type(query.filters) is not tuple or type(query.orders) is not tuple:
        raise TableSourceError("TABLE_QUERY_INVALID")
    if len(query.filters) > MAX_TABLE_FILTERS or len(query.orders) > MAX_TABLE_ORDERS:
        raise TableSourceError("TABLE_QUERY_LIMIT")
    for condition in query.filters:
        if type(condition) is not TableFilter or type(condition.column) is not str or type(condition.operator) is not str or condition.operator not in FILTER_OPERATORS:
            raise TableSourceError("TABLE_QUERY_INVALID")
        value = condition.value
        if condition.operator in {"IS NULL", "IS NOT NULL"}:
            if value is not None:
                raise TableSourceError("TABLE_QUERY_INVALID")
        elif type(value) not in {str, int, float, bool, Decimal, date, datetime}:
            raise TableSourceError("TABLE_QUERY_VALUE_INVALID")
        if type(value) is str and len(value.encode("utf-8")) > 16384:
            raise TableSourceError("TABLE_QUERY_LIMIT")
        if type(value) is float and not math.isfinite(value) or type(value) is Decimal and not value.is_finite():
            raise TableSourceError("TABLE_QUERY_VALUE_INVALID")
        if condition.operator in {"contains", "starts_with"} and type(value) is not str:
            raise TableSourceError("TABLE_QUERY_VALUE_INVALID")
    for order in query.orders:
        if type(order) is not TableOrder or type(order.column) is not str or type(order.descending) is not bool:
            raise TableSourceError("TABLE_QUERY_INVALID")
    if len({order.column for order in query.orders}) != len(query.orders):
        raise TableSourceError("TABLE_QUERY_INVALID")
    return query


@dataclass(frozen=True, slots=True)
class TableSourceOpenResult:
    metadata: TableSourceMetadata
    page: ManualQueryPage
    query: TableQuerySpec = TableQuerySpec()


def validate_table_target(
    ref: VerifiedTableRef,
    tree_target: TreeTarget,
    target: ManualSessionTarget,
    choice: ConnectionChoice,
) -> None:
    """Bind a tree-signed table to one captured manual-tab/profile scope."""

    if not isinstance(ref, VerifiedTableRef) or not isinstance(tree_target, TreeTarget):
        raise TableSourceError("INVALID_TABLE_REF")
    if not isinstance(target, ManualSessionTarget) or not isinstance(choice, ConnectionChoice):
        raise TableSourceError("INVALID_TARGET")
    if ref.target != tree_target:
        raise TableSourceError("TREE_TARGET_MISMATCH")
    provider = tree_target.provider
    mysql_scope = provider in {"mysql", "oceanbase_mysql"}
    oracle_scope = provider in {"oracle", "oceanbase_oracle"}
    if not mysql_scope and not oracle_scope:
        raise TableSourceError("UNSUPPORTED_PROVIDER")
    global_mysql_root = mysql_scope and not tree_target.database
    if (
        tree_target.connection_id != target.connection_id
        or tree_target.profile_revision != target.profile_revision
        or (tree_target.database != target.database and not global_mysql_root)
        or choice.connection_id != target.connection_id
        or choice.revision != target.profile_revision
        or choice.database != target.database
        or choice.provider.casefold() != tree_target.provider
        or choice.mode.casefold() != tree_target.mode
    ):
        raise TableSourceError("MANUAL_TARGET_MISMATCH")
    if mysql_scope:
        if global_mysql_root:
            # A whole-server tree root intentionally has no selected database.
            # Only a resolver-verified child can supply one, and it must match
            # both the explicit manual tab and immutable connection choice.
            if not target.database or ref.database != target.database:
                raise TableSourceError("TABLE_DATABASE_MISMATCH")
        elif ref.database != tree_target.database:
            raise TableSourceError("TABLE_DATABASE_MISMATCH")
        # In MySQL mode the database is the schema.  Keep both captured values
        # exact; never reconstruct the selected database from a display label.
        if target.schema not in {"", target.database}:
            raise TableSourceError("MANUAL_SCHEMA_MISMATCH")
        if ref.schema not in {"", target.database}:
            raise TableSourceError("TABLE_SCHEMA_MISMATCH")
    elif oracle_scope:
        if ref.database != tree_target.database:
            raise TableSourceError("TABLE_DATABASE_MISMATCH")
        # Oracle target.database is the service.  The owner comes from the
        # signed TreeNode record and must equal this tab's separately captured
        # schema byte-for-byte.
        if not target.schema or ref.schema != target.schema:
            raise TableSourceError("MANUAL_SCHEMA_MISMATCH")
    if ref.kind not in {"table", "view"} or not ref.raw_name:
        raise TableSourceError("INVALID_TABLE_REF")


def inspect_table(
    driver: TableSourceDriver,
    ref: VerifiedTableRef,
    target: ManualSessionTarget,
    choice: ConnectionChoice,
    dialect: SqlDialectPolicy,
    cancel_event: Event,
) -> TableSourceMetadata:
    """Read one table's bounded catalog facts using the manual connection."""

    tree_target = ref.target
    validate_table_target(ref, tree_target, target, choice)
    if not callable(getattr(driver, "execute", None)):
        raise TableSourceError("TABLE_DRIVER_UNAVAILABLE")
    provider = tree_target.provider
    if provider in {"mysql", "oceanbase_mysql"}:
        return _inspect_mysql(driver, ref, target, dialect, cancel_event, provider)
    if provider in {"oracle", "oceanbase_oracle"}:
        return _inspect_oracle(driver, ref, target, dialect, cancel_event, provider)
    raise TableSourceError("UNSUPPORTED_PROVIDER")


def build_table_select(
    metadata: TableSourceMetadata,
    dialect: SqlDialectPolicy,
) -> str:
    """Build the default browse query from verified identifiers only."""
    return build_table_query(metadata, dialect, TableQuerySpec())[0]


def build_table_query(
    metadata: TableSourceMetadata,
    dialect: SqlDialectPolicy,
    query: TableQuerySpec,
) -> tuple[str, Any]:
    """Compile AND conditions and stable ordering; user values are always binds."""
    query = validate_table_query(query)

    ref = metadata.signed_ref
    target = metadata.target
    if not metadata.columns:
        raise TableSourceError("TABLE_COLUMNS_UNAVAILABLE")
    try:
        select_list = ", ".join(dialect.quote_identifier(column.name) for column in metadata.columns)
        qualified_table = dialect.qualified_table(target, ref.raw_name)
        sql = f"SELECT {select_list} FROM {qualified_table}"
        columns = {column.name: column for column in metadata.columns}
        values = []
        clauses = []
        for condition in query.filters:
            column = columns.get(condition.column)
            if column is None:
                raise TableSourceError("TABLE_QUERY_COLUMN_UNAVAILABLE")
            name = dialect.quote_identifier(column.name)
            if condition.operator in {"IS NULL", "IS NOT NULL"}:
                clauses.append((name + " " + condition.operator, None))
                continue
            if not column.comparable:
                raise TableSourceError("TABLE_QUERY_TYPE_UNSUPPORTED")
            value = condition.value
            if condition.operator in {"contains", "starts_with"}:
                value = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                value = ("%" if condition.operator == "contains" else "") + value + "%"
                clauses.append((name + " LIKE ", " ESCAPE '!'"))
            else:
                clauses.append((name + " " + condition.operator + " ", ""))
            values.append(value)
        bound = dialect.bind_values(tuple(values))
        if len(bound.markers) != len(values):
            raise TableSourceError("DIALECT_BIND_MISMATCH")
        markers = iter(bound.markers)
        if clauses:
            sql += " WHERE " + " AND ".join(prefix if suffix is None else prefix + next(markers) + suffix for prefix, suffix in clauses)
        ordering = []
        for order in query.orders:
            column = columns.get(order.column)
            if column is None:
                raise TableSourceError("TABLE_QUERY_COLUMN_UNAVAILABLE")
            if not column.comparable:
                raise TableSourceError("TABLE_QUERY_TYPE_UNSUPPORTED")
            ordering.append(dialect.quote_identifier(column.name) + (" DESC" if order.descending else " ASC"))
        selected = {order.column for order in query.orders}
        ordering.extend(dialect.quote_identifier(name) for name in metadata.primary_key if name not in selected)
        if ordering:
            sql += " ORDER BY " + ", ".join(ordering)
        return sql, bound.parameters
    except (ChangeSetError, ValueError, TypeError):
        raise TableSourceError("TABLE_IDENTIFIER_UNSAFE") from None


def _inspect_mysql(
    driver: TableSourceDriver,
    ref: VerifiedTableRef,
    target: ManualSessionTarget,
    dialect: SqlDialectPolicy,
    cancel_event: Event,
    provider: str,
) -> TableSourceMetadata:
    _check_cancel(cancel_event)
    markers = dialect.bind_values((target.database, ref.raw_name)).markers
    if len(markers) != 2:
        raise TableSourceError("DIALECT_BIND_MISMATCH")
    object_sql = (
        "SELECT TABLE_TYPE FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = {markers[0]} AND TABLE_NAME = {markers[1]}"
    )
    object_rows = _run_catalog(driver, object_sql, dialect.bind_values((target.database, ref.raw_name)).parameters, 2, cancel_event)
    if len(object_rows) != 1:
        raise TableSourceError("TABLE_NOT_FOUND")
    object_kind = _text(_field(object_rows[0], 0), "TABLE_METADATA_INVALID").upper()
    expected = "VIEW" if ref.kind == "view" else "BASE TABLE"
    if object_kind != expected:
        raise TableSourceError("TABLE_KIND_MISMATCH")

    values = (target.database, ref.raw_name, MAX_TABLE_COLUMNS + 1)
    bound = dialect.bind_values(values)
    if len(bound.markers) != 3:
        raise TableSourceError("DIALECT_BIND_MISMATCH")
    column_sql = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, "
        "IS_NULLABLE, (COLUMN_DEFAULT IS NOT NULL), EXTRA, ORDINAL_POSITION "
        "FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = {bound.markers[0]} AND TABLE_NAME = {bound.markers[1]} "
        f"ORDER BY ORDINAL_POSITION LIMIT {bound.markers[2]}"
    )
    rows = _run_catalog(driver, column_sql, bound.parameters, MAX_TABLE_COLUMNS + 1, cancel_event)
    if not rows or len(rows) > MAX_TABLE_COLUMNS:
        raise TableSourceError("TABLE_COLUMNS_UNAVAILABLE" if not rows else "TABLE_METADATA_OVER_BUDGET")
    columns: list[TableColumnMetadata] = []
    for row in rows:
        if len(row) < 9 or _text(_field(row, 0), "TABLE_METADATA_INVALID") != target.database or _text(_field(row, 1), "TABLE_METADATA_INVALID") != ref.raw_name:
            raise TableSourceError("TABLE_METADATA_SCOPE_MISMATCH")
        name = _identifier(_field(row, 2))
        type_name = _text(_field(row, 3), "TABLE_METADATA_INVALID")
        data_type = _text(_field(row, 4), "TABLE_METADATA_INVALID").casefold()
        nullable = _mysql_bool(_field(row, 5))
        default_present = _mysql_presence(_field(row, 6))
        extra = _text(_field(row, 7), "TABLE_METADATA_INVALID", allow_empty=True).casefold()
        ordinal = _positive_int(_field(row, 8))
        generated = any(token in extra for token in ("auto_increment", "virtual generated", "stored generated")) or "on update " in extra
        default_generated = "default_generated" in extra
        if default_present or default_generated:
            has_default: bool | None = True
        elif nullable:
            # MySQL defines an omitted nullable column as NULL; its catalog
            # intentionally does not distinguish that implicit default from
            # an explicit DEFAULT NULL clause.
            has_default = True
        else:
            has_default = None
        editable_type = data_type not in _UNSUPPORTED_EDIT_TYPES and not data_type.startswith("geometry")
        comparable = data_type not in _UNSUPPORTED_COMPARE_TYPES and editable_type
        columns.append(
            TableColumnMetadata(
                name=name,
                type_name=type_name,
                nullable=nullable,
                has_default=has_default,
                generated=generated,
                editable=not generated and editable_type,
                comparable=comparable,
                required_on_insert=not nullable and not generated and has_default is not True,
            )
        )
        if ordinal != len(columns):
            raise TableSourceError("TABLE_METADATA_ORDER_INVALID")

    primary_key: tuple[str, ...] = ()
    if ref.kind == "table":
        values = (target.database, ref.raw_name, MAX_TABLE_COLUMNS + 1)
        bound = dialect.bind_values(values)
        key_sql = (
            "SELECT COLUMN_NAME, ORDINAL_POSITION FROM information_schema.KEY_COLUMN_USAGE "
            f"WHERE TABLE_SCHEMA = {bound.markers[0]} AND TABLE_NAME = {bound.markers[1]} "
            "AND CONSTRAINT_NAME = 'PRIMARY' "
            f"ORDER BY ORDINAL_POSITION LIMIT {bound.markers[2]}"
        )
        key_rows = _run_catalog(driver, key_sql, bound.parameters, MAX_TABLE_COLUMNS + 1, cancel_event)
        if len(key_rows) > MAX_TABLE_COLUMNS:
            raise TableSourceError("TABLE_METADATA_OVER_BUDGET")
        primary_key = _ordered_key(key_rows, name_index=0, position_index=1)
    return _make_metadata(target, ref, tuple(columns), primary_key, ref.kind == "table", provider, identity_known=True)


def _inspect_oracle(
    driver: TableSourceDriver,
    ref: VerifiedTableRef,
    target: ManualSessionTarget,
    dialect: SqlDialectPolicy,
    cancel_event: Event,
    provider: str,
) -> TableSourceMetadata:
    _check_cancel(cancel_event)
    bound = dialect.bind_values((ref.schema, ref.raw_name))
    if len(bound.markers) != 2:
        raise TableSourceError("DIALECT_BIND_MISMATCH")
    object_sql = (
        "SELECT OBJECT_TYPE FROM ALL_OBJECTS WHERE OWNER = "
        f"{bound.markers[0]} AND OBJECT_NAME = {bound.markers[1]} "
        "AND OBJECT_TYPE IN ('TABLE', 'VIEW') AND SUBOBJECT_NAME IS NULL"
    )
    object_rows = _run_catalog(driver, object_sql, bound.parameters, 2, cancel_event)
    if len(object_rows) != 1:
        raise TableSourceError("TABLE_NOT_FOUND")
    object_kind = _text(_field(object_rows[0], 0), "TABLE_METADATA_INVALID").upper()
    expected = "VIEW" if ref.kind == "view" else "TABLE"
    if object_kind != expected:
        raise TableSourceError("TABLE_KIND_MISMATCH")

    bound = dialect.bind_values((ref.schema, ref.raw_name, MAX_TABLE_COLUMNS + 1))
    if len(bound.markers) != 3:
        raise TableSourceError("DIALECT_BIND_MISMATCH")
    column_sql = (
        "SELECT OWNER, TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, "
        "DATA_SCALE, NULLABLE, DEFAULT_LENGTH, IDENTITY_COLUMN, VIRTUAL_COLUMN, COLUMN_ID "
        "FROM (SELECT c.OWNER, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.DATA_LENGTH, "
        "c.DATA_PRECISION, c.DATA_SCALE, c.NULLABLE, c.DEFAULT_LENGTH, c.IDENTITY_COLUMN, "
        "g.VIRTUAL_COLUMN, c.COLUMN_ID FROM ALL_TAB_COLUMNS c "
        "LEFT JOIN ALL_TAB_COLS g ON g.OWNER = c.OWNER AND g.TABLE_NAME = c.TABLE_NAME "
        "AND g.COLUMN_NAME = c.COLUMN_NAME "
        f"WHERE c.OWNER = {bound.markers[0]} AND c.TABLE_NAME = {bound.markers[1]} "
        "ORDER BY c.COLUMN_ID) WHERE ROWNUM <= " + bound.markers[2]
    )
    rows = _run_catalog(driver, column_sql, bound.parameters, MAX_TABLE_COLUMNS + 1, cancel_event)
    if not rows or len(rows) > MAX_TABLE_COLUMNS:
        raise TableSourceError("TABLE_COLUMNS_UNAVAILABLE" if not rows else "TABLE_METADATA_OVER_BUDGET")
    identity_known = provider == "oracle"
    columns: list[TableColumnMetadata] = []
    previous_ordinal = 0
    for row in rows:
        if len(row) < 12 or _text(_field(row, 0), "TABLE_METADATA_INVALID") != ref.schema or _text(_field(row, 1), "TABLE_METADATA_INVALID") != ref.raw_name:
            raise TableSourceError("TABLE_METADATA_SCOPE_MISMATCH")
        name = _identifier(_field(row, 2))
        type_name = _text(_field(row, 3), "TABLE_METADATA_INVALID")
        nullable = _oracle_bool(_field(row, 7))
        default_length = _optional_nonnegative_int(_field(row, 8))
        identity_flag = _oracle_yes_no(_field(row, 9), allow_unknown=not identity_known)
        virtual_flag = _oracle_yes_no(_field(row, 10), allow_unknown=False)
        ordinal = _positive_int(_field(row, 11))
        if virtual_flag is True or identity_flag is True:
            generated: bool | None = True
        elif identity_flag is False and virtual_flag is False:
            generated = False
        else:
            # OceanBase Oracle mode exposes IDENTITY_COLUMN as unsupported and
            # NULL in documented versions.  Keep these rows browseable, but
            # do not offer writes without a positive generated-column verdict.
            generated = None
        has_default = None if default_length is None else default_length > 0
        type_key = type_name.strip().casefold()
        comparable = type_key not in _UNSUPPORTED_COMPARE_TYPES
        editable_type = type_key not in _UNSUPPORTED_COMPARE_TYPES
        columns.append(
            TableColumnMetadata(
                name=name,
                type_name=type_name,
                nullable=nullable,
                has_default=has_default,
                generated=generated,
                editable=generated is False and editable_type,
                comparable=comparable,
                required_on_insert=not nullable and has_default is not True and generated is not True,
                data_length=_optional_nonnegative_int(_field(row, 4)),
                data_precision=_optional_nonnegative_int(_field(row, 5)),
                data_scale=_optional_nonnegative_int(_field(row, 6), allow_negative=True),
            )
        )
        if ordinal <= previous_ordinal:
            raise TableSourceError("TABLE_METADATA_ORDER_INVALID")
        previous_ordinal = ordinal

    primary_key: tuple[str, ...] = ()
    if ref.kind == "table":
        bound = dialect.bind_values((ref.schema, ref.raw_name, MAX_TABLE_COLUMNS + 1))
        key_sql = (
            "SELECT cc.COLUMN_NAME, cc.POSITION FROM ALL_CONSTRAINTS c "
            "JOIN ALL_CONS_COLUMNS cc ON cc.OWNER = c.OWNER "
            "AND cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME AND cc.TABLE_NAME = c.TABLE_NAME "
            f"WHERE c.OWNER = {bound.markers[0]} AND c.TABLE_NAME = {bound.markers[1]} "
            "AND c.CONSTRAINT_TYPE = 'P' AND c.STATUS = 'ENABLED' AND c.VALIDATED = 'VALIDATED' "
            f"AND ROWNUM <= {bound.markers[2]} ORDER BY cc.POSITION"
        )
        key_rows = _run_catalog(driver, key_sql, bound.parameters, MAX_TABLE_COLUMNS + 1, cancel_event)
        if len(key_rows) > MAX_TABLE_COLUMNS:
            raise TableSourceError("TABLE_METADATA_OVER_BUDGET")
        primary_key = _ordered_key(key_rows, name_index=0, position_index=1)
    return _make_metadata(target, ref, tuple(columns), primary_key, ref.kind == "table", provider, identity_known=identity_known)


def _make_metadata(
    target: ManualSessionTarget,
    ref: VerifiedTableRef,
    columns: tuple[TableColumnMetadata, ...],
    primary_key: tuple[str, ...],
    is_base_table: bool,
    provider: str,
    *,
    identity_known: bool,
) -> TableSourceMetadata:
    column_map = {column.name: column for column in columns}
    if len(column_map) != len(columns) or not set(primary_key) <= set(column_map):
        raise TableSourceError("TABLE_METADATA_INVALID")
    read_only_reason = ""
    verified: VerifiedTableMetadata | None = None
    if not is_base_table:
        read_only_reason = "该视图当前为只读。 / This view is read-only."
    elif not primary_key:
        read_only_reason = "此表没有可验证的完整主键，当前为只读。 / No verified complete primary key is available; this table is read-only."
    elif not identity_known or any(column.generated is None for column in columns):
        read_only_reason = "无法确认所有生成列信息，当前为只读。 / Generated-column metadata is incomplete; this table is read-only."
    elif any(not column.comparable for name in primary_key for column in (column_map[name],)):
        read_only_reason = "主键包含不支持安全比较的列，当前为只读。 / A primary-key type cannot be compared safely; this table is read-only."
    else:
        try:
            verified = VerifiedTableMetadata(
                target=target,
                table=ref.raw_name,
                columns=tuple(
                    VerifiedColumn(
                        column.name,
                        editable=column.editable,
                        comparable=column.comparable,
                        required_on_insert=column.required_on_insert,
                    )
                    for column in columns
                ),
                primary_key=primary_key,
                source_kind="verified_table",
            )
        except (ChangeSetError, TypeError, ValueError):
            read_only_reason = "表结构无法验证用于安全编辑，当前为只读。 / The table shape could not be verified for safe editing."
    return TableSourceMetadata(
        target=target,
        signed_ref=ref,
        columns=columns,
        primary_key=primary_key,
        is_base_table=is_base_table,
        verified_changeset=verified,
        read_only_reason=read_only_reason,
    )


def _run_catalog(
    driver: TableSourceDriver,
    sql: str,
    parameters: Any,
    limit: int,
    cancel_event: Event,
) -> list[tuple[Any, ...]]:
    _check_cancel(cancel_event)
    cursor = None
    try:
        cursor = driver.execute(sql, parameters)
        _check_cancel(cancel_event)
        fetchmany = getattr(cursor, "fetchmany", None)
        if not callable(fetchmany):
            raise TableSourceError("TABLE_DRIVER_INVALID")
        normalized: list[tuple[Any, ...]] = []
        while len(normalized) < limit:
            batch = fetchmany(min(128, limit - len(normalized)))
            _check_cancel(cancel_event)
            if batch is None:
                break
            if not isinstance(batch, Sequence) or isinstance(batch, (str, bytes, bytearray)):
                raise TableSourceError("TABLE_METADATA_INVALID")
            if not batch:
                break
            if len(batch) > limit - len(normalized):
                raise TableSourceError("TABLE_METADATA_INVALID")
            for row in batch:
                if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
                    raise TableSourceError("TABLE_METADATA_INVALID")
                normalized.append(tuple(row))
        return normalized
    except TableSourceError:
        raise
    except Exception:
        raise TableSourceError("TABLE_METADATA_QUERY_FAILED") from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass


def _ordered_key(rows: Sequence[Sequence[Any]], *, name_index: int, position_index: int) -> tuple[str, ...]:
    positioned: list[tuple[int, str]] = []
    for row in rows:
        if len(row) <= max(name_index, position_index):
            raise TableSourceError("TABLE_METADATA_INVALID")
        positioned.append((_positive_int(_field(row, position_index)), _identifier(_field(row, name_index))))
    positioned.sort(key=lambda item: item[0])
    if tuple(position for position, _name in positioned) != tuple(range(1, len(positioned) + 1)):
        raise TableSourceError("TABLE_METADATA_ORDER_INVALID")
    names = tuple(name for _position, name in positioned)
    if len(set(names)) != len(names):
        raise TableSourceError("TABLE_METADATA_INVALID")
    return names


def _field(row: Sequence[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _text(value: Any, code: str, *, allow_empty: bool = False) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", "strict")
        except UnicodeError:
            raise TableSourceError(code) from None
    if not isinstance(value, str) or (not value and not allow_empty) or "\x00" in value:
        raise TableSourceError(code)
    return value


def _identifier(value: Any) -> str:
    try:
        name = _text(value, "TABLE_METADATA_INVALID")
        from .changeset import _validate_identifier

        return _validate_identifier(name)
    except (ChangeSetError, TableSourceError):
        raise TableSourceError("TABLE_IDENTIFIER_UNSAFE") from None


def _mysql_bool(value: Any) -> bool:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", "strict")
        except UnicodeError:
            raise TableSourceError("TABLE_METADATA_INVALID") from None
    normalized = str(value or "").strip().upper()
    if normalized == "YES":
        return True
    if normalized == "NO":
        return False
    raise TableSourceError("TABLE_METADATA_INVALID")


def _mysql_presence(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", "strict")
        except UnicodeError:
            raise TableSourceError("TABLE_METADATA_INVALID") from None
    normalized = str(value or "").strip().upper()
    if normalized in {"1", "YES", "TRUE"}:
        return True
    if normalized in {"0", "NO", "FALSE"}:
        return False
    raise TableSourceError("TABLE_METADATA_INVALID")


def _oracle_bool(value: Any) -> bool:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", "strict")
        except UnicodeError:
            raise TableSourceError("TABLE_METADATA_INVALID") from None
    normalized = str(value or "").strip().upper()
    if normalized == "Y":
        return True
    if normalized == "N":
        return False
    raise TableSourceError("TABLE_METADATA_INVALID")


def _oracle_yes_no(value: Any, *, allow_unknown: bool) -> bool | None:
    if value is None and allow_unknown:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", "strict")
        except UnicodeError:
            raise TableSourceError("TABLE_METADATA_INVALID") from None
    normalized = str(value or "").strip().upper()
    if normalized == "YES":
        return True
    if normalized == "NO":
        return False
    if value is None and allow_unknown:
        return None
    raise TableSourceError("TABLE_METADATA_INVALID")


def _positive_int(value: Any) -> int:
    result = _optional_nonnegative_int(value)
    if result is None or result < 1:
        raise TableSourceError("TABLE_METADATA_INVALID")
    return result


def _optional_nonnegative_int(value: Any, *, allow_negative: bool = False) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TableSourceError("TABLE_METADATA_INVALID")
    if isinstance(value, str):
        text = value.strip()
        digits = text[1:] if allow_negative and text.startswith("-") else text
        if not digits.isdigit():
            raise TableSourceError("TABLE_METADATA_INVALID")
        result = int(text)
        if result < 0 and not allow_negative:
            raise TableSourceError("TABLE_METADATA_INVALID")
        return result
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise TableSourceError("TABLE_METADATA_INVALID") from None
    if result != value or (result < 0 and not allow_negative):
        raise TableSourceError("TABLE_METADATA_INVALID")
    return result


def _check_cancel(cancel_event: Event) -> None:
    if cancel_event.is_set():
        raise TableSourceError("CANCELLED")


__all__ = [
    "MAX_TABLE_COLUMNS",
    "TableColumnMetadata",
    "TableSourceError",
    "TableSourceMetadata",
    "TableSourceOpenResult",
    "TableFilter",
    "TableOrder",
    "TableQuerySpec",
    "build_table_query",
    "validate_table_query",
    "build_table_select",
    "inspect_table",
    "validate_table_target",
]
