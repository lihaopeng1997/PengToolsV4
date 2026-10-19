# Dedicated import transaction owner

## Scope

This package adds a non-UI owner for one approved relational file import. It
does not add an import panel or connect to a production database. It consumes
the existing immutable ApprovedImport plan and one-use source snapshot,
revalidates a signed ObjectTree table reference and fresh catalog facts, then
issues only append INSERT statements on its own injected connection.

The public API is ImportSessionRequest, ImportSession, and immutable
ImportSessionStatus, ImportRunResult, ImportDecisionResult, and
ImportCleanupResult values in tools.data_center.import_session.
Construction is inert. start() queues the owner worker and returns a future;
the worker can finish only at READY_TO_COMMIT, where the caller must separately
call commit() or rollback(). close() requests cancellation or rollback.
The owner retains its driver and stays alive after a failed close until
retry_cleanup() (or a repeated close()) obtains successful cleanup.

The injected driver_factory(target) must create a fresh, dedicated
ImportDriver. Its public adapter methods are configure_manual_commit,
execute, executemany(sql, parameter_rows), commit, rollback, and close. All
connection and cursor calls happen on the session owner thread. The service
never accepts or borrows a ManualSession.

## Import and transaction contract

Before opening a connection, the owner rejects an absent, closed, or already
consumed snapshot. It then resolves the tree-issued table node, validates the
selected target, and re-resolves the node after catalog inspection. The
metadata returned by inspect_table must match both the captured target and
the live signed reference. Views, non-base tables, unknown generation facts,
inconsistent null/default/required facts, duplicate catalog names, and
unmapped required columns fail closed. A table does not need a primary key for
append-only inserts.

The approved mapping must match fresh catalog type, nullability, precision,
and scale facts for every mapped column. Generated columns cannot be mapped.
Identifiers come only from the verified table/column metadata and pass through
the bound SQL dialect's identifier policy. Imported values are sent only as
bound typed rows. No source value is interpolated into SQL or included in safe
errors.

The input reader yields a bounded chunk at a time. Progress counts are
provisional DBAPI affected-row counts until the transaction decision; an
unknown or mismatched rowcount is a failure. The reader's terminal snapshot,
source fingerprint, and cancellation checks must pass before the owner offers
READY_TO_COMMIT. If a later source/cancellation/error check fails after a
chunk has been yielded, the caller's dedicated import transaction must
roll back; the reader cannot undo consumer writes. The import owner does that
rollback and never rolls back a manual-tab transaction.

Cancellation before the worker takes the future makes no database calls.
Cancellation during a blocking DBAPI call remains pending until that owner
call returns; cleanup does not run concurrently with a live cursor/connection.
If commit or rollback raises, state and transaction outcome become UNKNOWN.
The owner does not report a false success or retry a commit automatically.
Close failure retains the connection reference for an owner-thread retry.

## Supported target types

The initial conservative append mapping supports:

- MySQL and OceanBase MySQL: DECIMAL/NUMERIC(p,s) with p <= 65 and
  s <= 30; signed/unsigned integer types except TINYINT(1); CHAR,
  VARCHAR, TINYTEXT, TEXT, MEDIUMTEXT, LONGTEXT; DATE; DATETIME and
  TIMESTAMP.
- Oracle and OceanBase Oracle: bounded NUMBER(p,s) metadata with
  1 <= p <= 38 and nonnegative scale; CHAR, NCHAR, VARCHAR2, NVARCHAR2,
  CLOB, and NCLOB; DATE and TIMESTAMP are treated as explicit DATETIME
  targets to avoid dropping a time component.

Unsupported or incomplete metadata, including floating-point, binary/blob,
enum/set, unsupported temporal kinds, unbounded/unknown Oracle numeric
metadata, and unknown providers, is rejected. OceanBase metadata differences
that leave generated-column facts unknown remain non-writable.

## Verification and evidence boundary

python -m unittest tests.test_data_center_import_session -v passed 16 tests.
They use temporary CSV/XLSX files, actual signed ObjectTreeService selections,
and fake directory/DBAPI endpoints. One integration test builds the real
BoundManualTableScope and ManualRelationalProviderFactory, then exercises the
public ManualRelationalDriver.executemany adapter on the dedicated import
owner thread. Its Oracle profile defaults to LOGIN while the signed selected
owner is `Sales "East`; the test verifies live target revalidation, schema
selection, quoted INSERT identifiers, typed binds, and owner-thread DBAPI
calls; it also invalidates table selections after READY_TO_COMMIT and
confirms explicit commit and rollback still finish their own transactions. A
second public-factory test invalidates the signed scope inside the
connector, verifies setup fails before import SQL, and proves a close failure
retains the actual returned driver for an owner-thread retry. Coverage also
includes a whole-server MySQL `TreeTarget` captured through the actual
`ObjectTreeComposition` and `ObjectTreeService`: the signed database node
selects the nonempty database, and the real scoped factory imports and commits
through its public adapter. Constructor checks reject a mismatched nonempty
root and an empty Oracle root. Other coverage includes explicit commit, no-PK
append, Unicode and typed NULL/empty values, bounded multi-chunk rows, named
XLSX sheet, required-column and view rejection, late conversion/source-change
rollback, unknown rowcount, setup failure, queued/running cancellation,
cancellation racing commit-ready publication, manual-tab dirty-transaction
isolation, commit/rollback uncertainty, and retained-connection close retry.
python -m py_compile tools/data_center/import_session.py
tests/test_data_center_import_session.py also passed.

No live database, model, SSH target, production profile, user configuration,
or user data was used or verified. The public adapter is integrated against a
fake DBAPI endpoint; real provider/database acceptance remains outstanding.
A product UI is outside this foundation package.

## 2026-10-06 resume evidence boundary

The Oct 4 accepted hashes are historical references supplied with the task:
`tools/data_center/import_session.py` AA3614E59AA49313FD0F3C4113A57B2CA95539918B9B627DE43EF37C3325AFD6,
`tests/test_data_center_import_session.py` 7246B8D29C1B8B551AC096354C938CD1F0753C7ADC3C7190C8CE16D1B691AB6B,
and this report 2368B4EE1B0BB497A53600F9EA6860F89BDF6214D4AD238885A1C163593631CE.
The supplied Oct 4 evidence directory was empty on resume, so those earlier
hashes and the 16-test result above remain historical claims; the original
bytes and run log were unavailable for a fresh comparison. A separate Oct 6
resume snapshot was saved before this finalization at
`C:/Users/Lenovo/AppData/Local/Temp/pengtools-import-mysql-root-resume-20261006/`.
It is a record of the resumed working state, not a replacement for the missing
Oct 4 evidence. After the catalog-validation dependency was frozen, the
focused command
`.venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_import_session -v`
passed all 16 tests. This includes the whole-server MySQL composition path,
which uses the resolver-signed nonempty database node and verifies the fresh
scoped factory imports and commits only that captured database. The importer
and test files match their Oct 6 resume snapshot hashes below, so this final
pass required no importer or test edits:

- `tools/data_center/import_session.py` — E4E4234F76C4E3E64BDDA4236C883312E5D91A4FA8FA13ED6A909BD385814D40
- `tests/test_data_center_import_session.py` — A50FC2C0741BAD428BA94D9A1F9B2751614175E1A965715F41D811A3BC0D5A0D

## 2026-10-06 candidate API and dialog delta

The public `candidate_target_columns(TableSourceMetadata)` helper returns
frozen candidate records by reusing the importer’s existing conservative
column policy. It performs no driver work. It excludes generated/unknown or
unsupported fields with reason codes and blocks approval when a required
field cannot be represented. Candidate metadata remains display guidance;
`ImportSession` still resolves the signed reference and revalidates fresh
catalog facts at start. The candidate tests cover supported types, append
without a primary key, view/unknown-generation rejection, and unsupported
required-field reasons.

The separate PyQt6 import-dialog/controller foundation is documented in
`import-ui.md`. It uses this candidate API and the existing public provider
factory/import session. It does not change ManualQuery or host shutdown
wiring. Its registry close gate now distinguishes an ordinarily retained
connection in RUNNING/READY_TO_COMMIT from a terminal retained connection
awaiting cleanup; a delayed callback test checks that a completed cleanup
future cannot release references while the owner thread is alive.

The Oct 6 before-edit copies and manifest for these importer paths are in
`C:/Users/Lenovo/AppData/Local/Temp/pengtools-import-ui-before-20261006/`.
The previous Oct 4 evidence directory was empty on resume, as recorded above;
no missing Oct 4 source or acceptance artifact was reconstructed. The new
UI/candidate and XLSX row-width evidence is recorded in `import-ui.md` and
`import-preview.md`. The final focused command
`.venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_import_ui tests.test_data_center_import_session tests.test_data_center_import_preview -v`
passed 55 tests in both Windows and offscreen Qt runs. The final session-suite
contribution includes the frozen candidate helper regressions. No live
database or production profile was used. ManualQuery
entry and shutdown aggregation are not part of this delta.
