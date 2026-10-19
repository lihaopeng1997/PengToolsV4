# Manual-session full-query export bridge

## Public contract

`ManualSession.export_full_query(destination, format, target, *, overwrite=False, limits=None) -> Future[ExportReceipt]` queues export on the session's persistent DB owner. `destination` is a filesystem path, `format` is `csv`, `json`, or `xlsx`, and `target` is the writer's `ExportTarget(query_id, tab_id, generation)`. `limits=None` selects the frozen writer defaults. Construction and request validation do not touch a database or filesystem; the writer owns atomic publication and no-overwrite behavior.

`ManualSession.result_source_status` returns an immutable, lock-safe `ManualResultSourceStatus` without DB I/O. Its fields are `query_id`, `tab_id`, `generation`, `state`, `can_continue`, `can_export_full`, `cursor_open`, `page_count`, `retained_rows`, `retained_bytes`, `pending_rows`, `pending_bytes`, and `reason_code`. `ManualResultSourceState` values mean:

- `NONE`: no statement result has been captured.
- `AVAILABLE`: the typed prefix and the original cursor can continue; a full export is eligible.
- `BUDGET_LIMITED`: the visible 50 MiB result budget stopped paging, but a full export can resume from the retained candidate/prefetch and same cursor when `can_export_full` is true.
- `EXPORTING`: an export is consuming this source; UI paging/export is temporarily unavailable.
- `COMPLETE`: the cursor is closed and retained pages describe the complete result; it can be exported again.
- `EXPORTED`: this export consumed the live cursor; its pages were released and the source cannot be replayed.
- `UNAVAILABLE`: the source cannot prove a complete contiguous result; inspect `reason_code` and run a new user statement if appropriate.

GUI callers should read the status snapshot and pass the originating page's target to `export_full_query`; they should not infer full-result availability from the visible page's `has_more` bit. All query, fetch, export, transaction, and close work remains serialized by the owner. The owner rechecks query/tab/generation identity at dispatch. A queued operation that follows a query, transaction boundary, or source replacement cannot export the old target.

## Cursor and transaction behavior

The export consumes the exact cursor created by the successful statement and the exact typed prefix already returned to the UI. It never executes SQL again. A DML statement such as `UPDATE ... RETURNING` may be exported if it produced a typed rowset; export leaves the session `DIRTY` and performs no commit or rollback. A DML statement without a rowset has no export source. A completed, already-buffered result remains re-exportable; once export reads additional cursor data, success consumes that live source. If writing fails before cursor continuation starts, the source remains available. If a failure or cancellation occurs after continuation advances, the cursor is closed and the source becomes unavailable instead of pretending that the consumed prefix is a complete result.

Canceling an export future before its owner job starts leaves the current result source unchanged. Once the owner starts the export, its future is marked running and `Future.cancel()` returns `False`; call `cancel_current()` to request cooperative cancellation. The future stays pending until the owner operation returns. Cancellation removes the writer's partial temporary output; the owner closes/invalidates a cursor it has advanced. `close(disposition="cancel")` requests cancellation, waits for the real owner operation to return, then rolls back a dirty transaction and closes the driver on that owner. It does not terminate a blocked driver thread or report cleanup early. An optional driver's `request_interrupt()` hook may help unblock a call, but this package does not prove native cancellation support for any real driver.

The visible paging budget remains 50 MiB. The session retains at most 100,000 issued page objects, and the pending lookahead/batch buffer is capped at 501 rows and 50 MiB by estimated Python object size. Status reports retained and pending estimates separately. The DBAPI `fetchmany(n)` contract bounds the requested batch size to the active page remainder (at most 501); a driver that internally materializes extra rows or bytes is outside this Python-side estimate. If a returned candidate/tail cannot fit the pending cap, the source is marked `UNAVAILABLE` with `PREFETCH_LIMIT`; it is never silently dropped or exported as complete. The writer applies its own row/page/cell/output limits.

## Verification

TEMP fake DBAPI tests exercise three-page JSON readback without query replay; UI budget truncation followed by a contiguous full export; exact budget edge/lookahead handling; pending-prefetch fail-closed behavior; DML `RETURNING` without commit/rollback; no-rowset rejection; queued query identity drift; queued commit serialization; queued cancellation retaining the completed source; running-export Future state and cooperative cancellation during a blocked fetch; close waiting for the owner; fetch failure with no published partial; and result-status access from a Future callback without holding the session lock.

Commands and results on `.venv-build/Scripts/python.exe`:

- `-B -m unittest tests.test_data_center_manual_result_export tests.test_data_center_manual_session -v` — 30 tests passed, exit 0.
- `-B -m unittest tests.test_data_center_result_export -v` — 13 tests passed, exit 0.
- `git diff --check` — passed, exit 0. Baseline-to-worktree `git diff --no-index --check` comparisons for the three assigned files reported no whitespace errors (exit 1 for expected content differences).

All DB operations in these tests use in-memory fake DBAPI objects and all export destinations use `TemporaryDirectory`; no model, network, real database, user data, or user configuration is involved. No UI/full-query dialog or full-query UI bridge was changed. Production-driver interruption and driver-specific buffering behavior remain unverified.

## Baseline and source hashes

The worktree is `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`. Exact pre-edit copies for the original export implementation were stored at `C:\Users\Lenovo\AppData\Local\Temp\pengtools-manual-result-export-20261002-5f6537d29b8044ed9f998ab3b53f83cf`. Exact pre-edit copies for the owner-cancellation repair were stored at `C:\Users\Lenovo\AppData\Local\Temp\pengtools-export-owner-baseline-483cdcd2ec0a4ee4b50cc4868fafc945`.

Baseline SHA-256: `manual_session.py` `7C521011030DC135255B96F92FBA397E63ABEAE7CC96AF1F920D5653510A1DF1`; frozen `result_export.py` `B2E6E4A3721A093134D4BB3E9E9EE5AAC568B95834EDC1754C3A6129C59541FD`; frozen `manual_driver.py` `620CFF5866269452B87A79153689BAE8F79BE726B0545F7653AC0BFB7D013575`; frozen `manual_relational_providers.py` `B15B1A2E72822E1512591C05AA00B7725EE56A49FEA6C76E91191B776BDC9F2E`; frozen `test_data_center_manual_session.py` `66F4AD9DDC0856C0744CDD9F8BCD1F9441057773F92AFC7C0A092C90F88289D3`; frozen `test_data_center_result_export.py` `AC07A5F8FE4CB1CE039E78C7E8E77FCB3919718056A4C4D2478CFE1AED6E5EC2`.

Owner-repair pre-edit SHA-256: `manual_session.py` `0C38F9BFAD33217B90CAB98F0F2426DC4A45B5AC0737967605C84D8E2622F554`; `test_data_center_manual_result_export.py` `62C602D8DD54967ECD4E2F77B86AA8EDF8B1CB8918A9F632949F06E3D8CF9CDE`; this report `78B3BFF6ED98CCC6971D9DFB371346093C68C519F60424B45B5CAEAB97108C03`.

Final focused-file SHA-256: `manual_session.py` `4DD5EBFD01F0031664254F5FBDA99BF3A20BCC092A0FCE2415103647F2E56987`; frozen `manual_result_export.py` `D6D8705E99ED469C2BB2055551F7DA6F1E34A0FF96F5BE0646DB0213065F72D9`; `test_data_center_manual_result_export.py` `70BA8C58E0F671AEA2B58E9052711A8764297066D03D1BE3AAA2DC480C088BA1`.
