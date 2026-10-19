# Dameng provider implementation plan

Status: read-only planning package; no product or test files changed. Baseline is `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`. This plan does not claim that any DM integration has passed a real-driver or database test.

## Finding

Dameng is an implementation gap, not a database-transaction impossibility. The current read-only connector intentionally raises `DAMENG_SERVICE_TIMEOUT_UNVERIFIED`; manual-provider capability is `MANUAL_PROVIDER_UNSUPPORTED`; the metadata host has no Dameng catalog or directory template; and object-tree expansion reports that the Dameng directory adapter is not wired. The shared SQL policy already maps Dameng to a restricted Oracle-compatible subset. That policy must remain closed and must not be widened to obtain manual SQL support.

The highest-risk gap is connection routing and timeout ownership. The current read-only profile accepts a `host`/`server` value, but dmPython documents that value as either an IP/host or a DM service name. DM's service configuration can route a service name across multiple IPs, apply service-specific settings, and set `ADDRESS_REMAP` for DPI. The dmPython API documents that `login_timeout` maps to `CONNECT_TIMEOUT`, with service settings taking priority over the API setting when both are configured. `dmsvc_path` is not documented as a “disable all ambient config” switch: if the selected service entry is missing, the global file is used. Therefore, passing a profile host and timeout directly is not enough evidence that the connection reaches the fixed target within the host's budget.

The precedence conclusion is precise: official dmPython docs name `login_timeout`/`CONNECT_TIMEOUT` as an overlapping setting and document global < API < service-section priority. They do **not** list `connection_timeout` or `access_mode` in that overlap table, so this report does not claim that `dm_svc.conf` overrides those API fields. The DPI manual does list `ADDRESS_REMAP`; dmPython itself is documented as implemented through DPI. Treat the effect of global remapping on a direct dmPython host/port call as a routing risk/inference that needs a native test, not as a proven behavior for every dmPython version.

`Connection.dsn` is documented as a read-only address/port after connection. Checking it can reject an unexpected route before issuing SQL, but it is not a pre-authentication safeguard: it cannot prove that credentials were never sent to an unexpected endpoint. The generated configuration must therefore be proven to isolate routing before the connector is enabled.

## Current source seam

| Area | Current behavior | Required next implementation boundary |
|---|---|---|
| Read-only connection | `_connect_dameng` fails closed with `DAMENG_SERVICE_TIMEOUT_UNVERIFIED`; the common profile helper has port 5236 and accepts host/server plus username/user. | Add a worker-owned dmPython connector only behind a proved, fixed-route configuration seam. Preserve the current denial if the seam cannot prove endpoint and connect-timeout behavior. |
| Read-only adapter | A Dameng strategy exists with Oracle-compatible dialect and `SET TRANSACTION READ ONLY`, but its `access_mode` connector option is the string `"read_only"`, it has no timeout attribute, and it has no trusted native cancel method. The generic adapter's boolean-only autocommit readback is not adequate for dmPython's documented integer values. | Pass `dmPython.DSQL_MODE_READ_ONLY`; turn autocommit off through the actual dmPython attribute/constant and verify readback equals off; set the bounded execution timeout before each operation. Keep cancellation reported unsupported/unknown until a native API is verified. |
| Manual SQL | `inspect_manual_provider` rejects Dameng. The shared manual driver probes lowercase `autocommit`; it does not map dmPython's `autoCommit`. Its generic savepoint/release syntax is not a Dameng-specific contract. | Add a provider-specific adapter/classifier. Keep every DBAPI call in the tab owner; explicit `commit()`/`rollback()` only; protect transaction-control SQL; treat DM DDL/DCL implicit commits correctly. |
| Catalog/object tree | Dameng is absent from `host_metadata`'s relational provider/template sets. `object_tree` returns `dameng_directory_unavailable_readonly_connector_not_wired`. | Add only fixed, bounded user-catalog templates after read-only lease initialization. Do not add `ALL_*`/`DBA_*` access or give model tools broader schema authority. |
| Target scope | `DataCenterProfileHost._database_from_profile` checks generic `service_name`/`service`/`sid` before `schema`/`owner`; that order is not a valid Dameng schema rule. | Normalize DM endpoint and schema separately. Never reinterpret a DM service name, SID, or connection label as a schema. |
| Capability matrix | `engine_adapters` currently marks Dameng `supports_cancel=True`, while the read-only strategy exposes no cancel method and does not verify one. | Report no native cancellation until the actual driver path is verified; do not let the generic capability flag promise that a running query has stopped. |

## Bounded implementation plan

1. Keep DM fail-closed until a worker-owned configuration lease proves a fixed endpoint and bounded login behavior; never read or mutate the user's ambient `dm_svc.conf` as a workaround.
2. Define a DM endpoint DTO from the captured profile revision: direct host plus port (default 5236), or a separately explicit service route; legacy unclassified host values must not be guessed to be a service name.
3. Prefer an app-owned, per-lease `dm_svc.conf` directory with a collision-resistant service alias mapped to exactly one captured endpoint, passed through `dmsvc_path`; verify the documented fallback/override behavior against a conflicting synthetic ambient config before enabling it.
4. In the owner, lazily load dmPython, resolve the existing secret token there, pass finite `login_timeout=ceil(connect_timeout_s*1000)`, `connection_timeout=ceil(operation_timeout_s)`, `access_mode=DSQL_MODE_READ_ONLY` and `autoCommit=False`, then verify the effective connection target, access mode, timeouts, and autocommit before any query.
5. Keep read-only Agent policy unchanged: retain the restricted Oracle-compatible parser, initialize `SET TRANSACTION READ ONLY`, bind all values with DBAPI qmark `?`, and leave native cancellation unsupported until tested.
6. Add manual capability for exact `dameng/dameng` only: map and verify `autoCommit` off, use explicit `commit()`/`rollback()`, classify unknown syntax as UNKNOWN, fence raw transaction-control SQL, and classify DDL/DCL as implicit-commit hazards requiring the existing separate authorization path.
7. Implement bounded directory/search/describe using only verified user-scope catalog views; start with `USER_TABLES`, `USER_VIEWS`, and `USER_TAB_COLUMNS`, and refuse unverified cross-schema scope rather than falling back to `ALL_*` or `DBA_*`.
8. Add fake-driver tests for routing/config injection, integer autocommit readback, read-only enforcement options, finite timeout propagation, qmark/typed binds, manual transaction/savepoint/DDL behavior, catalog scope, drift, redaction, and cancellation-pending semantics.
9. Keep the capability disabled until a versioned dmPython/DPI runtime and dedicated disposable DM test database pass endpoint/config-precedence, read-only, per-call deadline, transaction/savepoint, current-schema/catalog, and disconnect/cancel acceptance.

## Minimum normalized profile and compatibility defaults

The existing persisted profile needs no secret-field change for a direct-endpoint prototype. The worker should consume this normalized, non-secret capture and resolve the secret only through the existing owner-side host seam:

| Field | Minimum/compatibility rule |
|---|---|
| `id` / connection ID, revision | Required and exact; use existing profile-host revision verification before creating the connection. |
| provider / mode / dialect | Canonical provider `dameng`, exact mode `dameng`, parser dialect `oracle` (restricted compatibility subset). Reject a mismatch; do not infer OceanBase mode. |
| endpoint | Required host and optional port; default port is 5236. Do not pass a raw profile `dsn`, URI, or connection string. A service alias is a distinct route contract, not a hostname fallback. |
| endpoint kind | Recommended new normalized field `direct` or `service`. If omitted on a legacy profile, accept only a direct endpoint form that the native driver/config test proves safe; otherwise return a stable unsupported-route code. Never silently reinterpret an old `host` string as a service route. |
| username | Required `username`/`user`; never default to SYSDBA even though dmPython's API has that default. |
| password | Existing encrypted secret token only; resolve inside the connection owner. Never put it in the generated service file, DTO, log, or error. |
| schema/owner | Optional `schema` or `owner`; when omitted, DM's documented default is the login user's default schema. Do not derive it from `database`, `service_name`, `service`, or `sid`. Verify the effective current schema before issuing scoped work. |
| timeouts | Use the existing host-owned defaults: 8 seconds for connect and 15 seconds for read/operation. Convert connect seconds to integer milliseconds for `login_timeout`; use integer seconds for `connection_timeout`. These remain fixed host options, not model/profile overrides. |
| service config | No persisted config-path default. Prefer a private generated config lease; service-file/HA support stays closed unless a separately captured profile revision and native precedence test prove the target and total retry bound. |

The 8s/15s values are current host defaults (`RelationalReadOnlyHost` and the manual factory), not values established by Dameng documentation. `login_timeout` is a login budget; `connection_timeout` is documented as an execution timeout in seconds. Driver-version tests must establish that the latter bounds both execute and fetch behavior expected by the host.

## Test and acceptance boundary

Fake-driver tests can prove local call arguments and state transitions, but cannot prove dmPython/DPI loading, `dm_svc.conf` precedence, endpoint routing, or server-side read-only enforcement. The fake module should expose the documented integer-valued `autoCommit` and `access_mode`, mutable timeout attributes, qmark cursor, `commit`/`rollback`, savepoint SQL, and typed values (`Decimal`, date, bytes, `None`, empty string). Tests should assert all cursor/connection calls stay on the captured owner thread; autocommit is verified before user SQL; manual DML remains DIRTY; one explicit commit performs one commit; DDL is blocked until separately authorized; and cancellation of a running call remains pending/unknown until the owner actually returns.

The narrow real-driver gate is a dedicated disposable DM instance and a packaged dmPython+DPI runtime. It must test a conflicting ambient/global service config and address remap against the app-owned `dmsvc_path` lease; demonstrate the observed server identity is the fixed test endpoint; measure failed-login and execute/fetch deadlines; verify the connection is read-only before a harmless attempted write; exercise commit/rollback/savepoint and DDL pre-commit behavior; inspect `current_schema` and the availability/contents of the selected USER catalog views; and record whether the driver exposes any owner-safe native cancel. No such real runtime/database was used for this planning package.

### Verified facts vs. outstanding evidence

- Official dmPython docs specify host/server, port 5236 default, `access_mode`, `autoCommit` (default TRUE), `connection_timeout` in seconds (0 means unlimited), and `login_timeout` in milliseconds (default 5000). The API mapping table explicitly maps `login_timeout` to `CONNECT_TIMEOUT`; overlapping settings use global < API < service-section precedence.
- Official docs show `DSQL_MODE_READ_ONLY`, `Connection.access_mode`, the `autoCommit`/`DSQL_ATTR_AUTOCOMMIT` property, `commit()`/`rollback()`, qmark positional binding, and bounded `fetchmany()`. This establishes API surface, not current application enforcement.
- Official transaction docs establish manual transactions and savepoints. They also say DM commits prior work before DDL; `DDL_AUTO_COMMIT` controls whether the DDL itself commits, while `ALTER TABLESPACE` and `ALTER USER` always auto-commit.
- Official DM FAQ documents `USER_TABLES` for the current user and `USER_TAB_COLUMNS` for column metadata. That is the constrained initial catalog path; schema semantics for a login user with multiple owned schemas still require a dedicated server test.
- Official dmPython introduction says dmPython calls the DPI interface and requires a DPI runtime. This machine's selected Python interpreter has no discoverable `dmPython` module (`find_spec` false); no vendor runtime or real database was tested.
- No `cancel` method appears in the official dmPython interface page searched for this report; the same page says `async_enable` is not supported. This is not proof that no vendor-specific interrupt exists. Keep cancellation unsupported/pending until the shipped driver version is checked directly.
- Outstanding route question: whether a controlled `dmsvc_path` alias can prevent ambient/global `ADDRESS_REMAP` and fallback behavior for every supported dmPython/DPI version. The official docs establish that the file exists, is read by the client, and can fall back to the global file; they do not provide a verified application isolation recipe. The generated-config lease in the plan is a testable local seam, not a verified guarantee.

## Primary Dameng sources

- [dmPython interface](https://eco.dameng.com/document/dm/zh-cn/pm/dmpython-interface.html) — connection arguments, timeout units/defaults, `dmsvc_path`, overlap priority, DBAPI constants/properties, transaction methods, qmark, and fetch methods.
- [dmPython introduction](https://eco.dameng.com/document/dm/zh-cn/pm/dmpython-introduction.html) — DB-API 2.0 and dependency on DPI runtime.
- [DM physical storage and client configuration](https://eco.dameng.com/document/dm/zh-cn/pm/physical-storage) — `dm_svc.conf`, service routing/retry settings, DPI `ADDRESS_REMAP`, `CONNECT_TIMEOUT`, and configuration scope.
- [DM Python service-name FAQ](https://eco.dameng.com/document/dm/zh-cn/faq/go-Python-new.html) — service-name host/server use and `dmsvc_path` example/fallback context.
- [DM transaction management](https://eco.dameng.com/document/dm/zh-cn/pm/management-affairs.html) — explicit transaction semantics and implicit commits before DDL.
- [DM savepoint syntax](https://eco.dameng.com/document/dm/zh-cn/pm/consistency-concurrency.html) — `SAVEPOINT`, `ROLLBACK TO SAVEPOINT`, and `RELEASE SAVEPOINT`.
- [DM database-check FAQ](https://eco.dameng.com/document/dm/zh-cn/faq/faq-db-check) — current-user `USER_TABLES` and `USER_TAB_COLUMNS` catalog examples.

## Local review boundary

Inspected only the DM-related portions of `readonly_relational_connectors.py`, `readonly_driver_adapters.py`, `manual_relational_providers.py`, `manual_driver.py`, `host_metadata.py`, `object_tree.py`, `profile_host.py`, `host_relational.py`, `engine_adapters.py`, and the six-engine requirements. No product files, tests, configuration, user data, credentials, sessions, or Git state were changed. No tests were run because this deliverable is a read-only implementation plan.
