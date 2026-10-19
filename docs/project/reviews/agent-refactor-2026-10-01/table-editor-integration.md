# Manual table editor integration

The Data Center tree can open a signed table or view into the existing SQL tab's `ManualSession`. The table UI uses that captured tab, connection, revision, database/schema, and owner for reads and writes; switching focus, SQL tabs, or workspaces does not retarget the owner or issue another query. Table edits remain a local overlay until the user previews and applies them through the existing savepoint path. An applied page becomes stale until the user explicitly refreshes it.

During table focus, the existing Commit, Rollback, Stop, and More controls move into the existing Run/New SQL toolbar row. The SQL tab bar remains visible on its own row, so users can create, switch, and close tabs while preserving the active table overlay. Returning to SQL restores the transaction row to its original position and shows the saved SQL text without executing it. No button callbacks are duplicated. The focus bookkeeping stores only widget IDs and scalar layout values, drops retired tab IDs, and restores only widgets still attached to the tab set.

The compact controls update their minimum height after application font or style changes, so the table's refresh and edit actions remain legible under the requested large fonts. In the accepted Windows QPA capture, all essential SQL, transaction, table, page, preview, discard, and status controls fit at 960×640 and 1160×720. At 960×640 the Chinese and English captures show one complete data row; at 1160×720 each shows four. The workspace scroll range is zero in all four captures. The captures use the calm theme, registered Microsoft YaHei UI, effective font sizes 17 and 21 pixels, and DPR 1.5; see the TEMP manifest for raw image dimensions and hashes.

`ManualShutdownSessionSnapshot.pending_local_changes` reports staged table edits separately from the database transaction state. A staged local overlay makes `can_commit` false but does not mark a clean transaction as dirty. Public `None`/`preflight` calls are read-only: they return `DECISION_REQUIRED` for a pending overlay without showing a dialog, discarding edits, opening a connection, or sending a transaction action. The workspace disposition uses a small owned `QDialog` with a full-width wrapped message, avoiding the empty icon column and narrow text area from the prior `QMessageBox`. Its compact role-styled buttons use short action captions, with complete accessible names and tooltips; local-only discard is distinct from database rollback, while the combined action explicitly names rollback and local discard. Keep is the default action and Escape rejects the choice. Commit remains disabled for local staged edits and UNKNOWN outcomes. The body explains that staged edits were not written and closing does not apply them, and that a rollback request cannot prove an unknown write was undone. Unknown or busy owner states are checked before any discard prompt or owner action.

The live UI route tests use `DataCenterPanel`, the signed production object tree, `ManualQueryHost`, the public table-source service, typed pages, and the production relational provider/session path with scripted TEMP connectors. The synthetic host factory explicitly calls `set_embedded_compact_mode(True)`, matching the embedded setup in `DataCenterPanel._default_host_factory`; the test does not use real profiles, config files, credentials, models, or a database. The Oracle route exercises the signed schema/table double-click through the public factory with service `TEMP_ORACLE_SERVICE`, login `LOGIN`, and exact owner `MiXeD Owner`; the captured service remains the database field and the quoted owner remains the schema target.

The UI fixture patches the dynamically imported splitter-layout load/save functions to per-test memory. It keeps those patches active through manual-owner `READY` confirmation, workspace/tree cleanup, deferred Qt deletion, and event processing, then restores them. The final fixture asserts that no manual session remains busy or retained and that workspace cleanup completed with no tree owners.

## Verification

Before the workspace-dialog geometry repair, the post-fixture-change integration run passed 13 tests:

```text
$env:QT_QPA_PLATFORM='windows'; .\.venv-build\Scripts\python.exe -B -m unittest tests.test_data_center_table_editor_integration tests.test_data_center_table_shutdown -v
Ran 13 tests in 10.077s — OK
```

That run includes the public MySQL table and read-only view routes, the actual public Oracle factory route, trimmed signed-target rejection, empty-table Qt row notifications, loaded-row search, long-value inspection, close/deferred-delete/SQL-return behavior, and local-overlay shutdown preflight/dialog checks. The MySQL public route also creates the four table-workbench Windows captures.

After the dialog repair, the changed shutdown module passed its three tests on Windows QPA:

```text
$env:QT_QPA_PLATFORM='windows'; $env:PENGTOOLS_TABLE_CLOSE_CAPTURE_DIR='C:\Users\Lenovo\AppData\Local\Temp\pengtools-table-close-dialog-captures-author-20261006'; .\.venv-build\Scripts\python.exe -B -m unittest tests.test_data_center_table_shutdown -v
Ran 3 tests in 2.168s — OK
```

The two decision tests show the real modal dialog, wait for layout, save screenshots, and check applied calm-theme body font (17/21 px Microsoft YaHei UI), full captions against button content rects, button and body bounds, minimum size hints, disabled Commit, default Keep, explicit local discard, Keep, and Escape. The design-system compact buttons use their 12 px compact action font; each caption and vertical text metric is checked against its actual content rect. Eight screenshots cover local-only and UNKNOWN-plus-local states in Chinese/English at both sizes. The Windows screenshot and geometry records are in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-table-close-dialog-captures-author-20261006`; dialog client sizes are captured in its manifest. The ten table-route integration tests were not repeated after this dialog-only change.

A broader 58-test run passed before the final test-fixture-only teardown tightening. It covered the table editor and shutdown tests plus manual-workbench integration, object-tree integration, workbench cleanup, and result-export UI. No product source changed after that 58-test run; both public production-route fixtures were rerun after the teardown tightening and passed as part of the final 13-test run.

## Review evidence and boundaries

The branch is `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`. The current source hash manifest and the owned diff against actual October 6 before-copies are in TEMP:

```text
C:\Users\Lenovo\AppData\Local\Temp\pengtools-table-ui-final-20261006\final-manifest.json
C:\Users\Lenovo\AppData\Local\Temp\pengtools-table-ui-owned-diff-20261006.patch
C:\Users\Lenovo\AppData\Local\Temp\pengtools-table-editor-ui-c7144b53bd33447abd1ac8a0a76e6308\manifest.json
```

The before copies are under `C:\Users\Lenovo\AppData\Local\Temp\pengtools-table-ui-20261006-resume`. Their hashes are recorded in the final manifest. The October 4 before directory was present but empty, so it is not a usable diff baseline and no old source was reconstructed. `object_tree_workspace.py` and the legacy quoted-Oracle test match their October 6 before-copy hashes; the public service getter and corrected legacy test were already present at resume. The new `table_editor.py` and shutdown test were absent from that snapshot.

The close-dialog repair has a separate exact before copy, diff, source hashes, and eight-capture manifest in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-table-ui-close-dialog-final-20261006`. Its before copies are in `pengtools-table-close-dialog-before-20261006`; this later snapshot is the comparison point for the QDialog geometry correction.

This phase does not change `MainWindow`'s global close flow and does not claim full application-wide shutdown aggregation. The accepted shutdown coverage is the per-workspace `DataCenterPanel` snapshot and disposition seam; global-close messaging that consumes the new local-stage field remains a separate acceptance boundary. No files were staged or committed.
