# DC-05 manual relational session foundation

## Scope and base

- Branch: `dev/2026-09-23`
- HEAD/base: `818fba72bf437de9bd49fcd5b198fb1270096a53`
- This increment implements the Python owner-thread/session foundation only. It does not wire the manual SQL tabs, panels, connection/profile loaders, or application shutdown path.
- It adds `tools/data_center/manual_session.py` and `tests/test_data_center_manual_session.py`. No database, user data, configuration, credentials, or real session was read or changed.

## API and transaction rules

Construct one `ManualSession` per manual tab with a fixed `ManualSessionTarget`, a target-bound `driver_factory`, and a vendor-aware `classify_sql(sql)` callback. The callback must return `StatementClassification(kind, effect)` using `StatementKind` and `TransactionEffect`; a display-oriented `is_read` heuristic is not sufficient. The target carries only tab/connection identity, profile revision, database, schema, and generation. The constructor performs no connection I/O. The factory, normal DBAPI execution, fetch, commit, rollback, savepoint, and close calls all run on the session's persistent owner thread.

The asynchronous operations are `submit`, `query`, `fetch_next_page`, `commit`, `rollback`, `run_savepoint_batch`, and `close`; each returns a `Future`. `cancel_current` stops queued work and requests cancellation of the active operation. `poll_cleanup` gives the host a nonblocking view of owner/connection retention and cleanup completion. An adapter may expose a nonblocking, thread-safe `request_interrupt()` hook; it must only interrupt the current operation and must not close the connection. The service does not kill threads or assume that a driver can interrupt a blocked call.

DML is dirty until explicit commit or rollback. DDL and DCL require separate explicit authorization and a clean transaction. Unknown-effect operations may run on a clean session, then leave it `UNKNOWN`; unknown or implicit-commit operations are blocked while the session is dirty. `SELECT FOR UPDATE` and `SELECT INTO` can be classified as `QUERY + DIRTY`; a data-modifying CTE is `DML + DIRTY`. Direct transaction-control SQL (including commit, rollback, savepoint, and autocommit changes) is rejected independently of the callback. A failed commit becomes `UNKNOWN` and is never retried. Rollback of an unknown outcome does not claim that the outcome became known.

Query results preserve native Python values in typed columns/rows. Pages are limited to 50, 200, or 500 rows, use owner-thread `fetchmany` with lookahead, and retain at most 50 MiB by the configured Python-object size estimate. A fetch error or cancellation leaves the previous successful page intact. `run_savepoint_batch(..., expected_rowcounts=...)` checks each expected count inside the owner-thread savepoint; zero, multiple, or unknown rowcounts become a conflict, and a failed batch rolls back only to its own savepoint. If that savepoint rollback fails, the session becomes `UNKNOWN` without falling back to whole-transaction rollback.

## Host integration contract

The future host adapter should create a new session for each tab generation and capture the exact connection/profile revision/database/schema in the factory closure. Resolve secrets inside that adapter and never put them in `ManualSessionTarget`, results, or callbacks. The adapter must disable and verify autocommit in `configure_manual_commit`, implement all connection/cursor calls on the owner thread, and supply explicit vendor transaction-effect classifications for lock reads, `SELECT INTO`, CTE DML, DDL/DCL, stored procedures, and implicit-commit statements. Do not pass `tools.sql_guard.classify_statement(...)["is_read"]` as proof of `NO_CHANGE`.

The Qt host should observe `Future` completion asynchronously, marshal results onto the UI thread, show separate DDL/DCL confirmations, and poll cleanup during tab/application shutdown. A dirty close must choose commit, rollback, or cancel. Choosing “cancel close” in the UI means do not call `close()` and keep the current session open. Calling `close(disposition="cancel")` instead requests cancellation of active work, then rolls back any dirty transaction and closes once owner-thread cleanup is safe; its Future may remain pending while an uninterruptible call is active. If a driver supports native cancellation, only its explicit `request_interrupt()` implementation may be invoked out-of-band; the host must still wait for `poll_cleanup()` to report completion. This package does not provide UI integration or driver-specific timeout policy.

## Verification evidence

All tests use an in-memory fake DBAPI. No network, live model, or real database was used.

| Command | Result |
|---|---|
| `python -m unittest tests.test_data_center_manual_session -v` | exit 0; 15 tests passed |
| `python -m py_compile tools/data_center/manual_session.py tests/test_data_center_manual_session.py` | exit 0 |
| `git diff --check` | exit 0; Git emitted existing LF-to-CRLF working-copy notices for unrelated dirty files |

The highest-risk regressions exercised are `test_dml_stays_dirty_until_one_explicit_commit`, `test_explicit_effects_cover_lock_select_into_cte_and_unknown_vendor_sql`, `test_failed_commit_becomes_unknown_and_is_never_retried`, `test_close_cancel_during_blocked_write_waits_then_rolls_back`, and `test_savepoint_rollback_failure_marks_unknown_without_full_rollback`.

## Limits and follow-up

This is a pure-Python execution/session primitive, not a completed manual-workbench feature. There is no production driver adapter or host assembly in this package. The fake driver verifies that an active call remains owned and visible as `CANCEL_PENDING`; it does not prove native driver cancellation, execution deadlines, real-engine DDL behavior, or Qt shutdown behavior. Driver integrations must provide and test these capabilities per engine before claiming them. The explicit classifier contract intentionally leaves unknown vendor syntax available on a clean session but marks the result outcome `UNKNOWN`, requiring explicit cleanup and preventing a false `CLEAN` state.
