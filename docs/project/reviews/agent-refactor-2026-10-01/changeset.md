# DC-05 typed relational ChangeSet foundation

## Scope and base

- Branch: `dev/2026-09-23`
- HEAD/base: `818fba72bf437de9bd49fcd5b198fb1270096a53`
- This increment adds the pure-Python row ChangeSet and an exact-rowcount guard at the manual session's savepoint boundary. It does not wire a result grid, metadata loader, driver, or Qt host.
- Files in this increment: `tools/data_center/changeset.py`, `tests/test_data_center_changeset.py`, the minimal `expected_rowcounts` addition to `tools/data_center/manual_session.py` and its focused test, plus the corrected close-cancel wording in `manual-session.md`.

## API and behavior

The host constructs `VerifiedTableMetadata` only after verifying a real base table and its complete primary key. `ChangeSet` rejects missing/unknown columns, unsafe identifiers, incomplete composite keys, noncomparable old-value conditions, and query-result/join/aggregate/unverified source kinds. `READ_ONLY_REASONS` and `readonly_reason()` provide explicit read-only explanations for those result shapes.

The caller injects an explicit `SqlDialectPolicy` for qualified table names, identifier quoting, and bound parameter syntax. The preview contains every statement and typed parameter. Values never enter the SQL text. Updates and deletes predicate on the complete primary key plus all configured comparable old values; `NULL` becomes `IS NULL` and empty strings remain ordinary bound values. An unchanged update stages nothing. Inserts also require the verified key, and every staged operation expects exactly one affected row.

`apply(session, confirmed_preview)` checks the full session target including profile revision, database/schema, tab id, and generation, then validates the preview fingerprint and revision. Stage/discard/reapply calls are rejected while a batch is active. Batch success clears the local changes; a rowcount conflict or batch failure keeps them staged and invalidates the prior confirmation. No automatic commit or retry occurs. `ManualSession.run_savepoint_batch(..., expected_rowcounts=(1, ...))` performs the count check on the owner thread before leaving the savepoint; zero, multiple, boolean, `None`, or other unknown rowcounts fail as conflicts. A successful batch leaves the enclosing manual transaction `DIRTY` for the user's explicit commit.

## Host integration contract

The metadata loader must produce `VerifiedTableMetadata` only for a verified base table with a stable full primary key. For arbitrary query results, joins, aggregates, missing keys, or unverified metadata, keep the grid read-only and surface the matching `readonly_reason`. `VerifiedTableMetadata` is a trusted host contract, not a cryptographic proof; callers must not construct it from arbitrary result columns.

The host should create one `ChangeSet` for its table-edit scope, stage copied native cell values, show the full `ChangePreview` including SQL, target, expected rowcount, and all typed parameters, then call `apply` only after the user confirms that preview. Marshal Future completion to the UI thread. On conflict keep the changes staged, refresh/reconcile the row, and require a new preview confirmation. Do not treat the `QmarkSqlDialect` test helper as evidence of vendor quoting or binding compatibility; implement and test the selected engine's `SqlDialectPolicy`.

The savepoint API applies all supported ChangeSet statements through the matching `ManualSession`; it never calls the legacy auto-committing console path and never commits the user's surrounding tab transaction. Unsupported vendor semantics, missing savepoints, or an unknown savepoint rollback outcome remain explicit failures/`UNKNOWN` states.

## Verification evidence

All ChangeSet tests use an in-memory fake DBAPI, a fake qmark dialect, and synthetic targets. They use no network, live database, or user configuration.

| Command | Result |
|---|---|
| `python -m unittest tests.test_data_center_changeset -v` | exit 0; 7 tests passed |
| `python -m unittest tests.test_data_center_manual_session -v` | exit 0; 15 tests passed |
| `python -m py_compile tools/data_center/changeset.py tools/data_center/manual_session.py tests/test_data_center_changeset.py tests/test_data_center_manual_session.py` | exit 0 |
| `git diff --check` | exit 0; Git emitted existing LF-to-CRLF working-copy notices for unrelated dirty files |

The main risk cases are `test_composite_key_update_preview_savepoint_then_explicit_commit`, `test_rowcount_conflict_keeps_changes_and_invalidates_prior_confirmation`, `test_stale_confirmation_and_wrong_generation_are_rejected_before_io`, `test_busy_changeset_rejects_edits_discard_and_reapply_until_batch_finishes`, and `test_null_is_is_null_empty_string_and_typed_parameters_are_preserved`. The session guard additionally tests 0, multi-row, and unknown rowcounts and verifies that savepoint conflicts do not roll back pre-existing transaction work.

## Limits

The verified metadata and dialect implementations are host responsibilities. Current evidence covers only in-memory fake execution and the qmark test policy; it does not establish real-engine optimistic locking, rowcount semantics, identifier quoting, savepoint support, or UI confirmation behavior. Vendor-specific adaptations and their real test environments remain follow-up work.
