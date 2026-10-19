# Manual CSV/XLSX import dialog foundation

Date: 2026-10-06

Baseline branch and HEAD: `dev/2026-09-23`,
`818fba72bf437de9bd49fcd5b198fb1270096a53`. The worktree already contained
other uncommitted project work; this phase changed only the importer-owned
paths listed below and left the rest untouched.

## Scope

This delta adds a reusable PyQt6 import dialog and controller for one captured
signed table selection, plus a public candidate-target helper. It does not
wire the dialog into ManualQuery, an existing data-center panel, the main
window, or application shutdown. Those existing UI seams remain owned by the
serialized table-workbench phase. No unrelated ManualQuery/panel/window files
were changed here.

The new files are `ui/data_center/import_controller.py`,
`ui/data_center/import_dialog.py`, and `tests/test_data_center_import_ui.py`.
The existing API delta is limited to `candidate_target_columns()` and its
frozen candidate DTOs in `tools/data_center/import_session.py`, plus tests in
`tests/test_data_center_import_session.py`.

## Behavior and safety boundary

`ImportTableContext` captures the initial `TableSourceOpenResult`, signed tree
target/node/generation, connection choice, profile host, and provider factory
inputs. The target candidates shown in the dialog are suggestions only; the
public `ImportSession` start path still resolves the captured signed
selection and inspects fresh catalog metadata before writing. Unsupported,
generated, or unknown fields remain excluded with reason codes. A required
unsupported field disables approval. A base table without a primary key can
still be an append target.

The dialog offers CSV encoding and delimiter controls, an exact XLSX sheet
name, source-index mapping (including duplicate headers), explicit NULL,
empty-string, and blank-cell rules, per-column date formats, typed bounded
preview, and import progress. Inspection and mapping preview use local Qt
workers. A run captures one immutable approval and a fresh unique import tab
ID. It uses a dedicated `ManualRelationalProviderFactory`; it never borrows a
ManualSession or enables autocommit. Commit and rollback are offered only in
READY_TO_COMMIT.

The controller delivers frozen progress values through Qt slots. Closing
during source work requests cancellation while the registry retains the
controller, worker, and dialog through GUI-thread terminal delivery. Import
session references are released only after both the cleanup future is done
and the owner thread is dead. Healthy RUNNING and READY_TO_COMMIT sessions
may retain their connection without being classified as failed cleanup;
terminal sessions retaining a connection block the close gate and expose an
explicit cleanup retry. A READY close requires an explicit rollback or
keep-open choice; UNKNOWN requires acknowledgement and stays tracked until
cleanup ends.

The dialog body is scrollable and its geometry uses the shared clamp helper
with no fixed height. NULL-policy options use separate bounded rows so their
labels remain readable at narrow widths and large fonts; CSV options and their
form label hide together in XLSX mode. The mapping header explains the `*`
required marker. Compact field labels show `DEC` for decimal targets, while
the selected-item tooltip preserves full `DECIMAL(precision,scale)` and
required-on-insert details. The offscreen geometry test checks that the action
controls remain within the dialog and that construction does not open a file.

A synthetic Windows-QPA visual pass applied the production
`ThemeManager.instance().apply(app, "calm", font_size=17/21)` to the fixture
for the 13/16-point stress cases. The theme's installed family list is
Microsoft YaHei UI followed by Segoe UI; on this host Qt selected Microsoft
YaHei UI for both locales. All 16 combinations (zh/en, 17/21 px theme body
size, 960/1160 logical width, CSV/XLSX) passed dialog and control bounds,
format-specific visibility, and vertical-scroll assertions. Top and bottom
screenshots (32 PNGs) and per-widget measurements are in
`C:/Users/Lenovo/AppData/Local/Temp/pengtools-import-final-20261006-winqa9/captures/`.
The explicit `QApplication.platformName()` assertion reported `windows`.
The available screen was 1707×1019 logical pixels at DPR 1.5. Each dialog was
960×760 or 1160×760 logical pixels (1440×1140 or 1740×1140 captured pixels).

The manifest records effective `QFontInfo` values for actual footer buttons
and NULL-policy checkboxes. The checkboxes inherit the calm 17/21-pixel body
font; footer buttons use the production `compactAction` rule (12 px) at both
stress sizes:

| Requested stress case | Calm body QSS size | NULL checkbox font (`QFontInfo`) | Footer font (`QFontInfo`) | Metrics height, logical / physical px (checkbox; footer) |
| --- | ---: | --- | --- | ---: |
| 13 pt | 17 px | Microsoft YaHei UI, 12.75 pt / 17 px | Microsoft YaHei UI, 9 pt / 12 px | 21.58 / 32.37; 15.23 / 22.85 |
| 16 pt | 21 px | Microsoft YaHei UI, 15.75 pt / 21 px | Microsoft YaHei UI, 9 pt / 12 px | 26.66 / 39.98; 15.23 / 22.85 |

The Qt font specifications are pixel-sized under calm styling (`QFont.pixelSize()`
is 17/21 for the checkboxes and 12 for footer buttons). The measured physical
metrics are logical heights multiplied by the captured DPR. The earlier
`winqa7` native-widget appearance pass is supplemental geometry evidence only;
the captures above are the calm-theme acceptance evidence. The calm captures
were visually checked at 960/16 stress in both languages and 1160/13 stress
in both languages. This exercises only the synthetic dialog fixture, not the
user application or any database.

A populated-state calm pass also used Windows QPA at 960×760 logical pixels
and 21-pixel body size for both zh and en. It wrote a temporary CSV with
duplicate `amount` headers, Decimal values, dates, explicit `NULL`, and a
blank value. The public controller `inspect_source()` and `preview_mapping()`
produced four indexed source rows, mapped indexes 0/1/3, and three typed
preview rows; the duplicate amount at index 2 remains skipped. The displayed
preview contained `Alpha / 1.20 / 2026-10-01`, `Beta / NULL / NULL`, and
`Gamma / NULL / 2026-10-02`. Both mapping and preview tables had zero
horizontal scroll, and the manifest asserts the completed status, footer,
enabled Start action, disabled Commit/Rollback actions, and zero connector
calls. Each locale case has top and bottom captures (four PNGs) in
`C:/Users/Lenovo/AppData/Local/Temp/pengtools-import-final-20261006-winqa18/captures/`.
These are temporary fixture data and do not contact a database.

## Verification

Command:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_import_ui -v
```

Result: 10 tests passed. They use temporary CSV/XLSX files, an actual signed
`ObjectTreeService`, a real `ManualRelationalProviderFactory`, and injected
fake DBAPI connections. Coverage includes duplicate source headers, typed
Decimal/date/NULL conversion, CSV empty-string policy, XLSX named sheets and
blank cells, source mutation rollback before any insert, a blocked insert and
Stop, explicit commit and rollback, a failed close and retry, owner retention
when a completed future callback runs before thread exit, cancellation while a
preview worker is closing, source-context isolation, and signed-tree
invalidation after preview. The invalidation case returns
`FAILED/TABLE_SCOPE_STALE`, deletes the approved snapshot, creates no import
session or registry owner, and performs no connector call. No live database,
configuration, production profile, secret, or user file was used.

The combined focused command for `tests.test_data_center_import_ui`,
`tests.test_data_center_import_session`, and
`tests.test_data_center_import_preview` was rerun after the compact mapping
label adjustment and passed 55 tests with both
`QT_QPA_PLATFORM=windows` and `QT_QPA_PLATFORM=offscreen`. The related backend
catalog/scope suite was independently run after its source freeze by the
backend owner (49 passed); this delta does not edit those files.

The UI foundation is not product-integrated yet. ManualQuery entry, parent
window/panel lifecycle wiring and workspace/tab/global shutdown aggregation
must be completed before claiming the end-to-end product flow.
