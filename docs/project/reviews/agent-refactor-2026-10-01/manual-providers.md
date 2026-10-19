# DC-05 manual relational provider assembly

## Scope and base

- Branch: `dev/2026-09-23`
- HEAD/base: `818fba72bf437de9bd49fcd5b198fb1270096a53`
- This increment adds a lazy provider/mode adapter for the existing manual session. It does not change the workbench panel, manual SQL dispatcher, profile store, `db_connect.py`, or the shared read-only Agent host.
- Files added: `tools/data_center/manual_relational_providers.py` and `tests/test_data_center_manual_relational_providers.py`.
- All connection profiles, credentials, DBAPI objects, and SQL in verification are synthetic. No network, live model, database, user configuration, or real session was used.

## Public API

`inspect_manual_provider(choice)` reports the capability of an immutable `ConnectionChoice` without reading a profile or opening a connection. `classify_manual_sql(choice, sql)` returns the selected backend's `StatementClassification`, including transaction effects; unsupported combinations remain `OTHER + UNKNOWN`. `ManualRelationalProviderFactory(choice, *, profile_host, connector, secret_resolver, connect_timeout, read_timeout)` is a lazy callable for `ManualSession`. `create_manual_session(target, choice, **factory_options)` creates a lazy one-tab session using that factory and classifier.

The caller captures one `ConnectionChoice`, then binds its `connection_id`, `revision`, and `database` plus the tab's `schema` and `generation` in `ManualSessionTarget`. The factory checks the target identity and delegates exact-revision/schema verification to `RelationalManualDriverFactory` before resolving a secret or invoking a connector. A revision or schema mismatch opens no connection. Profile loading, secret resolution, connector invocation, and all normal DBAPI/cursor/savepoint/commit/rollback/close operations stay on the tab's persistent owner thread. This factory does not accept or share a read-only Agent lease; each manual tab gets its own `ManualSession` and connection.

The adapter reuses the existing `ManualRelationalDriver` and vendor-aware `classify_relational_statement`; it does not create another execution loop. MySQL/Oracle native parameter shapes are passed through unchanged. The existing OceanBase Oracle ODBC connector wraps its cursor and translates numeric `:n` binds to ODBC qmarks. This package's fake DBAPI tests prove that typed parameters reach the adapter unchanged; the separate connector regression verifies that ODBC translation path.

## Capability matrix

| Provider / mode | Manual adapter | Transaction and savepoint behavior | Cancellation and timeout boundary |
|---|---|---|---|
| MySQL / `mysql` | Supported via the existing manual DBAPI adapter | Autocommit is disabled and verified; vendor-aware MySQL classifier; SQL `RELEASE SAVEPOINT` | No native interrupt; connect/read/write timeout options are passed to the direct connector, not a total operation deadline |
| Oracle / `oracle` | Supported via the existing manual DBAPI adapter | Autocommit is disabled and verified; Oracle classifier; `RELEASE SAVEPOINT` is intentionally a no-op because Oracle drops the marker on commit | No native interrupt; the direct connector applies connect and per-call timeout settings, not a total operation deadline |
| OceanBase / `mysql` | Supported as `oceanbase_mysql` | MySQL classification and savepoint behavior | No native interrupt; uses the existing MySQL connector timeout options |
| OceanBase / `oracle` | Supported as `oceanbase_oracle` only when its existing ODBC provider selection is present | Oracle classification; ODBC cursor preserves typed bind values while translating numeric placeholders; Oracle savepoint release is a no-op | No native interrupt; existing ODBC login and statement timeout settings must be applied by the connector |
| Dameng | Not yet supported by the manual provider factory | No transaction or savepoint capability is claimed | No capability is claimed |

All four wired providers refuse to begin until the DBAPI adapter can disable and verify autocommit. DML leaves the tab `DIRTY` and never auto-commits. Only the user's explicit `commit()` changes it to clean. Oracle/OceanBase Oracle DDL/DCL classification reports implicit commit; MySQL's temporary-table DDL and unrecognized syntax stay `UNKNOWN`. Locks, `SELECT INTO`, modifying CTEs, and direct transaction-control syntax use the shared explicit classifier/session guards rather than an `is_read` heuristic.

Dameng's capability decision is based on the current manual integration surface: `RelationalManualDriverFactory` currently admits only MySQL, Oracle, and the two OceanBase modes. `tools/db_connect.py` has a legacy `dmPython` connection path, but that does not establish that a manual owner can bound connection setup, configure/verify autocommit, or safely classify transactions. The read-only connector's `DAMENG_SERVICE_TIMEOUT_UNVERIFIED` policy is a separate Agent connector restriction and is not used as evidence about manual transaction support. This is a conservative manual-factory gap, not a claim that Dameng lacks transaction features; a dedicated bounded manual adapter remains required.

The direct connector rejects unsupported TLS/SSH/DSN-style profile options instead of silently dropping them. `supports_native_cancel` is false for every provider. The session can keep an uninterruptible call as `CANCEL_PENDING` until it actually returns; no native cancellation or hard deadline is established here. Per-driver timeout options are not a total query deadline.

## Verification evidence

The Oracle vertical test constructs the session without I/O, then exercises synthetic profile load, secret resolution, connector creation, typed `SELECT`, DML, explicit commit, and close on one owner thread. It verifies there is no automatic commit and that the selected manual connection is separate from a synthetic Agent lease object. Additional fakes exercise all four provider/mode pairs, profile and schema drift before connector invocation, autocommit verification failure, Oracle's no-op savepoint release, MySQL's SQL savepoint release, and backend-aware SQL effects.

| Command | Result |
|---|---|
| `python -m unittest tests.test_data_center_manual_relational_providers -v` | exit 0; 8 tests passed |
| `python -m unittest tests.test_data_center_manual_session tests.test_data_center_manual_integration tests.test_data_center_profile_host tests.test_data_center_readonly_relational_connectors -v` | exit 0; 50 tests passed |
| `python -m py_compile tools/data_center/manual_relational_providers.py tests/test_data_center_manual_relational_providers.py` | exit 0 |
| `git diff --check` | exit 0; Git printed existing LF-to-CRLF working-copy notices for unrelated dirty files |

The existing connector test covers Oracle timeout setup and OceanBase Oracle numeric-bind translation. These are source/fake-connector regressions, not real-driver compatibility evidence.

## Integration contract and limits

The manual SQL host should create `create_manual_session(target, choice, profile_host=host, ...)` once per tab/generation, use `inspect_manual_provider` before exposing execution, and attach Future completion to its existing UI worker. It must never route SQL through `run_console_statement`, which auto-commits DML. DDL/DCL confirmation, transaction close decisions, application-exit cleanup polling, and multi-statement/procedure-block behavior remain host responsibilities. A dirty tab stays dirty after a successful savepoint batch until the user commits.

Evidence is limited to injected in-memory DBAPI implementations. The package does not prove real MySQL, Oracle, or OceanBase login/statement timeout behavior, vendor rowcount/savepoint semantics, native cancellation, SSH/TLS transport, or UI lifecycle integration. Dameng remains unsupported pending a separate manual driver and bounded-connect/autocommit/transaction tests. Stored blocks and “execute all” are outside this increment and remain in scope for later host integration.
