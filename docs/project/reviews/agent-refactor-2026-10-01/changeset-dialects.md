# DC-05 vendor ChangeSet dialects

## Scope and base

- Branch: `dev/2026-09-23`
- HEAD/base: `818fba72bf437de9bd49fcd5b198fb1270096a53`
- This increment adds provider-bound `SqlDialectPolicy` implementations for a verified row ChangeSet. It does not change `manual_driver.py`, the SQL query route, profile loading, or UI files.
- Added `tools/data_center/changeset_dialects.py` and `tests/test_data_center_changeset_dialects.py`; made a focused identifier and bind-template update in `tools/data_center/changeset.py` and `tests/test_data_center_changeset.py`. Updated the owned relational-provider test fixture to model Oracle current-schema read/ALTER/recheck and each mode's correct target scope.
- Before changing the owned ChangeSet files, copied the prior versions to `C:\Users\Lenovo\AppData\Local\Temp\pengtools-changeset-dialects-phase-20261001`. Baseline SHA-256: `changeset.py` `FC9E10D00893E8E101E8E4C23A45ED421724931F9344D0C97A1C7D148FC15D3C`; `test_data_center_changeset.py` `23A8271FBB594F06333AB703E07058FF9E6E45639459BAB6F0F22A2E0A8DBD0B`.
- Verification uses synthetic targets and in-memory DBAPI shims only. No real database, credentials, user configuration, or network connection was used.

## Public API and target binding

The host calls `create_changeset_dialect(choice, target)` and passes the returned policy to `ChangeSet(verified_table_metadata, policy)`. Construction and SQL preview perform no I/O. The factory accepts only exact canonical provider/mode/dialect combinations and requires `ConnectionChoice.connection_id`, `revision`, and `database` to match the manual target. A policy also rejects later calls for a different target. Unsupported combinations, including Dameng, raise `UNSUPPORTED_SQL_DIALECT`; there is no qmark fallback.

| Provider / mode | SQL placeholders | Bound parameters | Qualified table |
|---|---|---|---|
| MySQL / `mysql` | `%s` | positional tuple; PyMySQL-compatible | individually backtick-quoted `database`.`table` |
| OceanBase / `mysql` | `%s` | positional tuple; same MySQL path | individually backtick-quoted `database`.`table` |
| Oracle / `oracle` | `:p0`, `:p1`, … | named mapping keyed by `p0`, `p1`, … | explicitly captured `schema`.`table`, or unqualified `table` |
| OceanBase / `oracle` | `:1`, `:2`, … | positional tuple, then the existing ODBC cursor converts numeric binds to `?` | explicitly captured `schema`.`table`, or unqualified `table` |

The OceanBase Oracle bind shape follows the existing direct connector: `_OceanBaseOdbcCursor._translate_numeric_binds` translates numeric `:n` placeholders to ODBC qmarks and passes positional values through. The policy rejects an OB Oracle quoted identifier containing a `:1`-style token before execution, avoiding ambiguity with this existing translator. It does not change or claim named-bind support in the ODBC wrapper.

Oracle qualification uses only the raw schema in `ManualSessionTarget.schema`. The service/database value is bound into the target but is never treated as an Oracle owner. The current profile helper returns a raw owner (quoted profile syntax such as `"AppOwner"` becomes `AppOwner`; an unquoted name is uppercased). The manual driver verifies `CURRENT_SCHEMA`, applies an explicitly selected owner if needed, and verifies it again before user SQL. The joint synthetic regression passes that raw owner through `qualified_table()` and confirms the service name is not used as a schema. MySQL treats database and schema as the same scope and rejects a nonempty `target.schema` that differs from `target.database`; an empty MySQL database is rejected unless a fixed schema supplies that scope.

## Identifier and value safety

Metadata identifiers must be text, nonempty, at most 256 characters, and NUL-free. They may contain Unicode, spaces, dots, quote characters, percent signs, semicolons, and the former `{bind}` marker text. Each metadata value is escaped as exactly one identifier component: dots inside a table name are not split into SQL qualification, embedded double quotes double for Oracle/standard quoting, and embedded backticks double for MySQL. Since PyMySQL formats the entire query with `%`, MySQL identifier percent characters are doubled in the query template and verified to return to literal `%` through local `Cursor.mogrify()`.

ChangeSet SQL is now assembled from structured string and bind-marker pieces rather than splitting on a textual `{bind}` token. A metadata name containing `{bind}` cannot create or remove a placeholder. `Decimal`, `date`, `bytes`, empty strings, and ordinary values remain in the bound tuple/mapping; `None` remains an `IS NULL` condition where it is part of optimistic old-value matching. Hostile value text is never concatenated into the SQL template.

Identifiers are still a trusted metadata boundary: this layer does not discover or verify a real table or primary key. The host must construct `VerifiedTableMetadata` only from verified base-table metadata and a complete key. A string that resembles SQL is safely quoted as one metadata identifier, but this policy does not make arbitrary caller-supplied names verified.

## Driver API evidence

The PyMySQL Cursor API documents that tuple/list arguments use `%s` placeholders, dictionary arguments use `%(name)s`, and `mogrify()` returns the formatted query without executing it: [PyMySQL Cursor Objects](https://pymysql.readthedocs.io/en/latest/modules/cursors.html). The installed PyMySQL Cursor's `mogrify()` was also exercised locally with a minimal fake connection object; no socket or database was created.

The python-oracledb bind guide documents colon-prefixed placeholders and named binding where dictionary keys match placeholder names: [python-oracledb Using Bind Variables](https://python-oracledb.readthedocs.io/en/latest/user_guide/bind.html). The pure Oracle policy follows this mapping form. The OceanBase Oracle policy instead follows the existing local ODBC wrapper described above.

## Verification evidence

The fake vertical tests apply ChangeSets through the actual `ManualRelationalDriver.execute()` boundary, then check rowcount-one savepoint success, a still-`DIRTY` tab, and explicit commit. Oracle reaches its driver as named mapping; MySQL reaches PyMySQL as `%s` plus positional tuple; OceanBase Oracle passes through the existing `_OceanBaseOdbcConnection`/cursor wrapper and reaches the fake raw cursor as qmarks plus the same typed tuple. The tests also check no service-name schema substitution, mixed-case owner qualification, scope/mode rejection, SQL-looking metadata, quoted names, bind-marker text, MySQL percent handling, and conservative rejection of OB Oracle numeric-bind identifier collisions.

| Command | Result |
|---|---|
| `python -m unittest tests.test_data_center_changeset_dialects tests.test_data_center_changeset -v` | exit 0; 17 tests passed, including quoted mixed-case profile owner → raw target schema → qualified SQL |
| `python -m unittest tests.test_data_center_manual_relational_providers -v` | exit 0; 8 tests passed with current-schema read/ALTER/recheck and MySQL database scope in the fake provider matrix |
| `python -m unittest tests.test_data_center_manual_session -v` | exit 0; 17 tests passed |
| `python -m py_compile tools/data_center/changeset.py tools/data_center/changeset_dialects.py tests/test_data_center_changeset.py tests/test_data_center_changeset_dialects.py tests/test_data_center_manual_relational_providers.py` | exit 0 |
| `git diff --check` | exit 0; no whitespace errors (Git printed existing LF→CRLF working-copy notices for unrelated dirty files) |
| Scoped Python/Markdown trailing-whitespace and final-newline check | exit 0; all six owned source/test/report files clean |

## Limits

All database execution remains fake. The PyMySQL `mogrify()` check verifies the local installed driver's formatting path without connecting. The Oracle named mapping and OceanBase ODBC rewrite are checked through fake DBAPI cursors, not real Oracle/OB instances. Real-engine quoting limits, null semantics, rowcount behavior, driver compatibility, and database effects require dedicated environments. Production UI wiring and end-to-end ChangeSet usage remain with the host/UI work.
