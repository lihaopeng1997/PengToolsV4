# Query-history metadata persistence — 2026-10-06

This increment implements the §7.4 history metadata contract for the reusable query-history component. It adds optional row count and bounded evidence-summary persistence, displays those fields in Chinese and English, and keeps record restoration as a data-only signal to the caller. Product entry-point wiring and host shutdown aggregation remain pending; this report does not claim end-to-end history integration.

The repository target is `D:\PengTools`, branch `dev/2026-09-23`, HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`. The worktree was already broadly dirty. The phase changed only `tools/data_center/workspace_store.py`, `tests/test_data_center_workspace_store.py`, `ui/data_center/query_history.py`, `tests/test_data_center_query_history_ui.py`, this report, and the existing query-history UI report. No commit or Git publication was performed.

## DTO and persistence contract

`HistoryRecord` retains its existing positional fields and appends `row_count: int | None = None` and `evidence_summary: tuple[HistoryEvidenceSummary, ...] = ()`, preserving older callers. The new frozen `HistoryEvidenceSummary` contains only `evidence_id`, a bounded identifier-shaped `tool_name`, a short `state_code`, optional non-negative `count`, and an exact boolean `truncated` flag.

The store accepts only an exact tuple of at most 16 exact evidence DTOs with unique safe IDs. Counts are exact integers (booleans are rejected) in the range 0–1,000,000,000. Evidence JSON is a fixed five-key shape, parameterized on write, and capped at 8 KiB. Unsupported keys, values, mutable lists, duplicate IDs, malformed JSON, or invalid stored metadata fail closed. SQL and NoSQL text continue through the existing credential-text scanner. No result values, raw result sets, model prose/reasoning, credentials, parameters, worker/session objects, or transaction state have fields in this DTO or table.

## Schema migration

New files use schema version 2. A version-1 database is migrated inside one SQLite transaction after checking the application ID, the exact owned table names/column signatures, and the required history indexes. The migration adds nullable `row_count` and non-null `evidence_summary` with `NULL`/`[]` defaults, then updates `user_version`. Drafts, layout, history text/context/status, favorite flags, and indexes remain in place. Store-size limits are checked before the migration commits.

Foreign and newer versions remain rejected. A malformed version-1 or version-2 table shape returns `SCHEMA_MALFORMED`; there is no destructive repair or fallback. Reading malformed persisted metadata returns the safe `STORE_RECORD_INVALID` code. The temporary migration test constructs an actual version-1 SQLite file and checks that old drafts, layout, and favorites survive, unknown row count/evidence defaults are returned, a metadata-bearing upsert survives close/reopen, and favorites remain set. A deliberately malformed version-1 fixture is rejected with its file bytes unchanged.

## Display and restoration

The browser shows row count as localized `unknown`/`未知`, zero, or its numeric value. It shows up to two evidence summaries inline and indicates any additional count; the metadata tooltip contains the complete tuple with ID, tool, state, optional count, and truncation status. Detail content remains bounded by the DTO limits. The UI still emits the original immutable `HistoryRecord` when the caller requests a new draft; it does not execute SQL, open a connection, mutate the target revision, or restore transaction state.

The narrow UI fixture exposed that setting a font on the browser alone left its child controls at the application default. `QueryHistoryBrowser.setFont` now propagates the selected font to its own search, metadata, detail, and action controls. The product-equivalent Windows capture fixture first applies `ThemeManager.apply(app, "calm", font_size=...)` globally and then constructs the browser, which applies its local calm token stylesheet. It checks the effective `QFontInfo` values for the search button, metadata label, SQL detail editor, and restore button rather than inferring from the requested font or filename.

## Verification and visual evidence

All SQLite cases used fresh temporary files. The UI uses synthetic history rows and a temporary SQLite store; it used no user data, live database, configuration, model, network, credentials, or real session.

| Command | Result | Log |
| --- | --- | --- |
| `.venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_workspace_store -v` | 15 tests, zero skips, 0.800 s, exit 0 | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-history-metadata-store-20261006.log` (SHA-256 `B582BC210765748B65FC388FA592517DF5170D7F4F12E06D037F2AC1292F09F8`) |
| `QT_QPA_PLATFORM=windows .venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_query_history_ui -v` | 9 tests, zero skips, 2.503 s, exit 0; fixture confirmed `QApplication.platformName() == "windows"` and `ui_theme == "calm"` | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-history-metadata-ui-windows-20261006.log` (SHA-256 `86268FBDF07B3F591D1321F28454AA19032C5EA32BE40102BA954BA509F453BE`) |
| `.venv-build/Scripts/python.exe -B -m py_compile tools/data_center/workspace_store.py tests/test_data_center_workspace_store.py ui/data_center/query_history.py tests/test_data_center_query_history_ui.py` | exit 0 | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-history-metadata-pycompile-20261006.log` |

The test registered the installed Segoe UI and Microsoft YaHei application-font families. It applied the global calm stylesheet with 17 px and 21 px stress-font settings. At 150% device scale, the 960×640 logical fixtures produced 1440×960 captures; the 1160×720 fixtures produced 1740×1080 captures. `QFontInfo` reported `Microsoft YaHei UI` and effective control sizes of 12.75 pt / 17 px and 15.75 pt / 21 px. The quarter-point difference is the Windows Qt resolution observed in the controls; the explicit pixel sizes were asserted. The four current globally themed captures were visually inspected for metadata readability, scroll bounds, and reachable controls:

| Fixture | Effective controls | Capture | SHA-256 |
| --- | --- | --- | --- |
| Chinese, 960×640 logical | 12.75 pt / 17 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-zh-960-font13.png` | `2D810232D3F3B463B6F3B28D7B03663D928C350D2ACBA32EE57E6BD0559E596D` |
| English, 960×640 logical | 12.75 pt / 17 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-en-960-font13.png` | `05C927F97BDCC1B502076E7A1EEAAF9BE1BC6453F1F612290693E4B131BEDDF3` |
| Chinese, 1160×720 logical | 15.75 pt / 21 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-zh-1160-font16.png` | `EB331C67C16112A4CDA665A94A35D7E5AADB15068638852F942FB982A5520A18` |
| English, 1160×720 logical | 15.75 pt / 21 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-en-1160-font16.png` | `FE509E4C20A5E9120691809038CCAB61501D812F6C4FEF69FF30966DB18B106B` |

The machine-readable capture manifest, including selected QPA, global calm theme, registered font families, logical and physical sizes, and effective fonts for four controls in every fixture, is `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/capture-manifest.json` (SHA-256 `E98C050E60F4B3BA1A68B5035AEF84A72D114121AB1A955138C2D508996F03CE`). These current fixtures supersede the earlier helper-slice screenshots for effective font-size evidence; the earlier 3/3 close/restore/layout acceptance remains historical lifecycle evidence.

The before copies and manifest captured at phase start are in `C:/Users/Lenovo/AppData/Local/Temp/pengtools-history-metadata-20261006/before/`. A bounded before/after diff and final source-hash manifest are retained in that phase's `final/` directory.

## Current hashes and integration boundary

Use the final phase manifest for the exact six-file hashes and the hashes of the read-only UI dependencies. The old history-entry caller still needs to capture and pass the execution's SQL, target, status, row count, and evidence summary to this API. That later integration must bind the captured operation record rather than sampling the currently visible editor after asynchronous completion. This increment does not wire that caller or alter the existing ManualQuery, panel, MainWindow, importer, or table paths.
