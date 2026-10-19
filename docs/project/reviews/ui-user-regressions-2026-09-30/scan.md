# Requirement folder scan regression — 2026-09-30

## Finding

The user described selecting an existing folder, then seeing no imported item in Requirement Management. The exact user folder and timing were not available, so the user's exact occurrence is not claimed as reproduced. A matching loss path was reproduced with TEMP fixtures: while a quiet file-tree `SvnWorker` was active, the scan button remained enabled and the folder chooser returned the selected path, but `_start_task` rejected the scan because another worker was active. No scan worker ran and the ledger did not change. The pre-fix Windows-QPA trace is preserved at `%TEMP%\pengtools-scan-flow-20260930-v5\report-v5.json`.

The observed callback also protects against a separate visibility case: search, kind, status, and system filters can hide a newly imported row. Before the change, a successful merge refreshed the tree without resetting those stale filters or selecting the imported record.

## Change

`RequirementPanel` now keeps one pending folder selection while a quiet file-tree worker runs. Later selections replace that queued folder and show explicit feedback. Once the active worker finishes, the scan starts before a pending file-tree refresh; the existing long-task guard still refuses to queue behind a long operation. Closing the panel drops its pending scan, and dispatch validates that the selected directory still exists.

After a successful save, the panel finds the imported or updated row by local path/SVN URL, clears stale list filters, and uses the existing focus path to select it. This changes only presentation state; existing requirement status, release/online dates, and month data remain governed by the existing merge/inference behavior. Partial SVN metadata failures preserve the local path and are reported with a warning rather than a success toast. Empty/invalid results and save failures continue to abort before success feedback; candidate save failure leaves the ledger unchanged.

## Verification

The new `tests.test_requirement_scan_live_runtime` suite drives `scan_btn` through an actual Qt `QFileDialog` (the dialog uses `DontUseNativeDialog` and a timer chooses a TEMP folder), a real `SvnWorker`/`QThread`, the real scan function, TEMP ledger persistence, and visible tree selection. It does not invoke SVN, network, external processes, production paths, or the UI5 application.

Measured fixture counts and worker behavior:

- Empty ledger: 0 rows before scan, 1 persisted and visible selected row after scan.
- Existing-row scan: 1 row before and after; ID/title/status/actual release date/actual online date/month remain unchanged, and the row becomes visible and selected through stale filters.
- Quiet-worker race: 2 seeded rows become 3 after choosing and coalescing two scan roots; only the latest chosen root is scanned. The old file-tree callback arrives with its original path after selection changed and is ignored. Event order is old file worker completion → queued scan → pending refresh of the selected result. There is no second active `SvnWorker` while the first is running.
- Cancelled chooser, long-task guard, panel close with a queued scan, and partial SVN-information failure all have focused assertions. Partial failure retains the local path and emits a warning, not a success toast.
- The 56-test focused bundle leaves `_ACTIVE_SVN_WORKERS` empty; runtime tests also assert the panel worker and pending flags drain.

The final focused command passed with exit 0 (56 tests):

```powershell
& '.venv-build/Scripts/python.exe' -m unittest tests.test_requirement_scan_live_runtime tests.test_requirement_scan_folders tests.test_requirement_save_failures tests.test_requirement_focus_section tests.test_requirement_worker_lifecycle -v
```

`py_compile` for `panels/requirement_panel.py` and `tests/test_requirement_scan_live_runtime.py` passed with exit 0. The quiet-worker race also passed once under `QT_QPA_PLATFORM=windows`; an independent application probe reported `platformName=windows`, `Microsoft YaHei UI`, 9 pt, DPR 1.5. That automation still uses the Qt widget dialog with `DontUseNativeDialog` and programmatically selects a TEMP fixture; it is not evidence from the Win32 OS-native folder picker. Logs are `%TEMP%\pengtools-scan-live-windows-qpa-race-20260930.log` and `%TEMP%\pengtools-scan-live-windows-qpa-evidence-20260930.txt`.

The original `before.json` snapshot was not modified. Its pre-change hash for `panels/requirement_panel.py` is `CEF3F5885DCF1BE94826D540182B81EF73FE4E677528C95B8B52952145A9DF89`. Final owned-source hashes after verification:

- `panels/requirement_panel.py`: `BDD6FAFA11C7CC57CAB36CF8964E414F7F99BB6B93738D3F1E58D027BAA12121`
- `tests/test_requirement_scan_live_runtime.py`: `2B0C415648859F8C567CD246C42C2FD58DBC233405C84DA000299066B0D80D42`

The user's reported application crash was not reproduced in this scan-only fixture work. This verifies the folder-selection race, persistence, and row visibility paths with synthetic TEMP data; it does not establish the exact user's folder, original crash stack, or behavior of the Win32 native chooser.
