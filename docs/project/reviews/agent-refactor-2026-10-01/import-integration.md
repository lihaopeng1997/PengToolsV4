# Table import product integration evidence — 2026-10-06

This phase connects the accepted CSV/XLSX importer to the live Data Center table workflow and shutdown gates. The checkout remains on `dev/2026-09-23` at base `818fba72bf437de9bd49fcd5b198fb1270096a53`; the workspace was already dirty. The phase-start copies and byte hashes are preserved separately from earlier component evidence.

## Product behavior

The visible table Import action captures the selected signed table context and the parent SQL tab/generation. It opens a parentless import controller and dialog with their own import ID/generation, then uses the public factory and a dedicated `ImportSession`. Switching tabs or refreshing the tree cannot retarget an approved import. A stale target after preview fails safely, closes the approved snapshot, and does not open a connector.

`ImportHost` retains the dialog, controller, worker, session, and connection through cleanup. A ready import contributes a dirty decision with `busy=False`; preview, insert, decision, and active cleanup contribute pending work. Unknown outcomes block group commit. Explicit rollback only rolls back reversible sessions and acknowledges/releases unknown import owners without sending another database rollback. Failed connection close remains visible for retry. Collection requires a completed cleanup Future and a dead owner thread, and parent-tab deletion does not destroy the parentless import QObjects.

The manual-workbench gate blocks import inspect/preview/start/commit/rollback and table edit/apply/refresh paths while a shutdown attempt is frozen; Stop/cancel remains available. Tab, workspace, and application shutdown use fresh aggregate preflight before any explicit decisions. Main’s shutdown signature and prompt include local table edits as unsaved changes that are not auto-applied and prevent commit. Cancel is the default and focused action; Esc cancels. Unknown-import wording describes acknowledgement/release, not a new rollback or a known undo.

## Verification

The final focused offscreen run passed 26 tests in one process: all import-host integration tests, the component importer UI tests, all Data Center application-shutdown tests, the visible table-button-to-READY route, and three manual workbench shutdown/gate tests. The separate Windows QPA capture passed 1/1 after the final dialog change. `git diff --check` exited successfully; Git printed line-ending normalization notices for other already-dirty files.

The integration regression covers mixed READY/UNKNOWN owners with zero partial commit, UNKNOWN acknowledgement with zero extra rollback calls, failed-close retry without a false busy state, and the interval where both decision and cleanup Futures are done while the owner thread is still alive. During that interval the public registry snapshot and host retain the actual controller/dialog; after owner exit and dialog hide they retire. Another case delivers `DeferredDelete` to a parent widget and verifies the parentless Qt objects remain usable.

All product-route fixtures use temporary CSV files, in-memory profile/tree configuration, and fake provider connections. No real user configuration, database, credentials, or model was opened.

## Windows visual evidence

The final capture used `QT_QPA_PLATFORM=windows`, asserted `QApplication.platformName() == "windows"`, and applied the production Prism `calm` theme. At logical 960×640 and 1160×720, the actual visible Import button, populated mapping/preview dialog, and Main shutdown modal were captured in Chinese and English. The stress body sizes were 17 px and 21 px; the Import dialog’s nullable checkbox matched those effective font sizes. The Main modal used actual two-row local edits reported by `ManualQueryHost.manual_shutdown_snapshot()`, plus an UNKNOWN import fixture.

Main modal logical sizes were zh 400×302 and 460×374, en 460×318 and 460×407. Every action button met its minimum size hint and stayed inside the dialog; modal dimensions fit the available screen. The capture also verified the complete labels, local-unsaved explanation, UNKNOWN acknowledgement wording, focused/default Cancel action, and Esc cancellation. Captures were rendered at DPR 1.5.

The final visual run and copied PNGs are in the evidence directory recorded below. Its `windows-capture-manifest.json` has the exact per-image paths, geometry, font sizes, row counts, theme, QPA platform, and DPR. Component/parser acceptance remains recorded separately in `import-ui.md` and is not counted as this product-integration acceptance.

## Follow-up lifecycle and visual freeze

The follow-up closes three lifecycle gaps found in review. A READY import is now eligible for an explicit workspace-group commit only after the whole group passes fresh preflight; dispatch commits the dedicated import connection once and never sends imported rows or a commit through the parent SQL session. UNKNOWN still blocks a mixed commit before any owner is dispatched. A dead terminal owner with its import dialog still open now contributes `READY_TO_CLOSE`, and an explicit host/workspace close retires that dialog. The fast standalone UNKNOWN acknowledgement path synchronizes its acknowledged state before the next parent preflight, without sending another rollback. Importer-specific workspace copy says “acknowledge and release” and states that it sends no new database rollback and does not prove the write was undone.

The final follow-up focused run passed 24 tests. It includes the real table Import button through `DataCenterPanel.manual_shutdown_snapshot()` and `manual_shutdown_cleanup("commit")`, import-connection-only commit assertions, mixed UNKNOWN zero-partial-commit, owner/Future retention, retryable close failure, terminal-dialog retirement after either whole-host commit or rollback, and a dirty parent table update plus a separately committed import closed through the actual workspace group-commit flow. It also covers standalone UNKNOWN acknowledgement followed by one parent workspace close without another UNKNOWN prompt. The final Windows QPA run passed both the populated product-route/shutdown capture and the UNKNOWN workspace-confirmation capture. These follow-up totals are distinct from the earlier component and integration runs above.

The settled Windows product captures use `QT_QPA_PLATFORM=windows`, assert the Windows QPA platform, and apply the production Prism `calm` theme. The host fixture is 960×640 or 1160×720 logical pixels; the actual import dialog is 1160×760 logical pixels in both cases. Four populated mapping and four populated preview images show four mapping rows and one preview row at effective body/NULL-checkbox fonts of 17 px and 21 px, DPR 1.5. The NULL checkbox uses a pixel-sized `QFont` (`pixelSize` 17/21, `pointSizeF` −1). Each footer control uses Microsoft YaHei UI at 12 px with 15 px font metrics (`pointSizeF` −1); visible footer controls satisfy their minimum size hints and stay inside the dialog. Capture settling is 320 ms before measuring and saving the fixed title/header, plus 120 ms after scroll. In the 21 px case, the title rect is `[24,18,1112,27]` inside header `[20,16,1120,64]` and dialog root `[0,0,1160,760]`; in the 17 px case it is `[24,18,1112,22]` inside header `[20,16,1120,54]` and the same root. Thus the earlier apparent top-edge crop was a capture-settling artifact; no dialog geometry change was needed.

The follow-up workspace confirmation captures cover Chinese and English at 17 px and 21 px, with actual dialog sizes from 448×190 through 460×377 logical pixels, minimum size hints from 272×102 through 272×107, and a 460 px maximum width. Effective body font is 17/21 px; the production calm theme’s action font remains 12 px. All action controls fit their minimum size hints, Cancel/Keep stays the safe default, and the dialog fits the available screen. These captures concern the compact workspace decision helper, not the frozen UI10 close-action dialog.

The latest follow-up captures and full final manifest are archived separately from the original 16-path phase-start snapshot at the path recorded below. The original archive and its before hashes remain unchanged. A pre-follow-up byte copy for `tests/test_data_center_workspace.py` was not preserved before that test file’s narrow regression edit; the follow-up manifest identifies that missing baseline rather than inferring one. Root acceptance remains pending.

## Original integration-phase files and hashes

These SHA-256 values compare the original integration phase-start snapshot with that phase’s final files. The baseline snapshot contains the pre-existing dirty state at that phase’s start; it is not the Git base commit. The current follow-up final hashes for all 18 integration paths are in the separate manifest below.

| File | Original phase-start SHA-256 | Original phase final SHA-256 |
|---|---|---|
| `ui/data_center/manual_query.py` | `4c460a03db15c849fd0feb596499cb3a8b5682f73a4232331f34830d6845c06d` | `a781df3ddb88ae98c3b57ed90dfb30d30a7d48ab4723117db8f183efa3990875` |
| `ui/data_center/table_editor.py` | `655b7e64f0e3b49f18e66a91fdfc57a83ee8ab202c3ee6e6bbd7ba4a2b11791c` | `01e6963aad599c7355032249ebf00a0be30f3908ed3b7d7cb7fe4d1ed29f6f79` |
| `main_window.py` | `52b43c2d03d728733e9b8e1fb02a28a5f054bb8d03a29c99eb2f71c2e10d07b0` | `b90186a9ab7d6cb738653ac22703c0a85bd5e2c5ffc13a7cbf32c62a9e90501d` |
| `ui/data_center/import_controller.py` | `32a3d42cb1ad34ea30973043da487f2f31617b1b840c5f1ba0d67fe11fbbe708` | `3da115147665b624c123fc8255bc7dfda739386882081ef0a4bd4f36312e052a` |
| `ui/data_center/import_dialog.py` | `0d45be0437890d5c9f272eb7909e1ac961ebc174e63cf792acc69902592f7fa1` | `09fc3a55600bf40970e83130be568242e7b88f718a9f2bce2dbfcbcee140249e` |
| `ui/data_center/import_host.py` (new) | — | `3edb9c365a9fadda2d871e946240ea4f59cc71d2e0b8e99018ac86fdebdccc6c` |
| `tests/test_data_center_import_integration.py` (new) | — | `74a15ca2f5437f7165e131e1d5fb082793c3f53824d667beb8a41d23f62be8a6` |
| `tests/test_data_center_application_shutdown.py` | `c2bf2445ce0bb78dd637d01ff57a516543bdf7ea3087d15dcc67058f162135bf` | `266e26cb33b77af4ba96c460bd8f4c2b7c85b5e2e135a379e2efe1dd02c3cc77` |
| `tests/test_data_center_table_editor_integration.py` | `71fc1e61ef6a93e4b743e7d2fd77e3c0f57b28a8596886a09ec712a2eaf213f0` | `e8adee5ed0ca82135b801275de0d55537c3de9d6b5d8fb5c95088f16258b81cf` |

`panels/data_center_panel.py`, the earlier component-level `tests/test_data_center_import_ui.py`, and the existing manual-integration test file are unchanged from this phase’s start. Their earlier dirty changes were preserved.

The before copies, unified phase diffs, captured images, test logs, and complete final hash manifest are archived at:

`C:\Users\Lenovo\AppData\Local\Temp\pengtools-import-integration-final-20261006-36fbfb23aaed47e7b63c6d19375404d7`

The follow-up captures, test logs, full final source hash manifest, and available follow-up before copies are archived separately at:

`C:\Users\Lenovo\AppData\Local\Temp\pengtools-import-integration-followup-final-20261006-8c23`

Root acceptance is still pending. No files were staged or committed, and no push or external database action was performed.

## Workspace-close UNKNOWN copy follow-up — 2026-10-06

This copy-only follow-up shortens the workspace decision body when an importer has an UNKNOWN result. It states the importer acknowledgement/release, lack of a new database rollback, and inability to prove the write was undone once. Mixed manual/import UNKNOWN state still separately says that manual rollback is only a request. When staged local edits exist, the message retains their count, that closing will not auto-apply them, that the chosen close action discards them, and that Keep preserves them. The mixed manual/import action caption includes both rollback and acknowledgement even when the dirty count is zero. Action mapping, default Keep, Escape behavior, lifecycle code, and dialog geometry were unchanged.

Before editing, exact copies and hashes were saved separately at `C:\Users\Lenovo\AppData\Local\Temp\pengtools-import-workspace-copy-followup-20261006-184331\before`. This snapshot records the live panel hash `8C8DD8B9B140762E5A10D582EA9B12EC801A982B752883A3B5970A6BCC882458`, table-shutdown test hash `6EB24F3BE1389EA32535932C35D71D3ACE131EF3A20CB67864744F0D8CA67BD6`, and report hash `247C1508CD67546F32CBCCC8E99B17698516922AA5EF0F061C0BA254FF6A332E`. These are this follow-up's immediate before values; the original integration-phase hashes remain as listed above.

The Windows QPA verification ran `tests.test_data_center_table_shutdown` with four tests passed and zero skips. The test asserted `QApplication.platformName() == "windows"`, applied the production Prism calm theme, and captured the importer-only and mixed manual/import UNKNOWN messages in Chinese and English at 17 px and 21 px. All captures used Microsoft YaHei UI at the expected body size; action labels used the production 12 px font, fit their measured content rects, and satisfied minimum size hints. The Keep action remained the default. QPA was Windows and the measured DPR was 1.5.

| Workspace decision case | zh 17 px | zh 21 px | en 17 px | en 21 px |
|---|---:|---:|---:|---:|
| Import UNKNOWN only | 400×146 | 400×188 | 400×168 | 453×188 |
| Manual + import UNKNOWN, two staged edits | 458×190 | 460×242 | 460×256 | 460×323 |
| Manual + import UNKNOWN, no staged edits | 400×168 | 400×215 | 448×190 | 459×242 |

The reported importer-only English 21 px dialog decreased from 454×377 to 453×188 logical pixels. The new captures, measurements, test log, exact before copies, per-file diffs, final source copies, and full 18-path final hash manifest are in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-import-workspace-copy-followup-20261006-184331`. The preceding product-integration 30/30 Windows lifecycle receipt remains at `docs/project/reviews/agent-refactor-2026-10-01/evidence/root-import-product-20261006.json`; it predates this copy-only follow-up and is not counted as its test result. Root's acceptance of this follow-up is pending. No files were staged or committed.
