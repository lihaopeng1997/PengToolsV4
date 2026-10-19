# Data Center manual-session lifecycle and Agent layout checkpoint

Branch `dev/2026-09-23`, base HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`. The checkout remains dirty from the larger shared refactor; this checkpoint did not stage, commit, push, or alter user configuration/data.

## Changes in this checkpoint

`MainWindow` now waits for manual-session cleanup before database-workbench and Agent cleanup. It preflights the already-created owners, gates new manual work, compares the snapshots again before dispatching a decision, and treats `ready` as the only terminal success. A `ready_to_close` action response keeps owner references and schedules another nonblocking poll. An unknown response aborts the exit attempt and never advances to Agent cleanup or `quit`. The legacy database-worker fallback retains its panel reference and polls `isRunning()` without waiting or terminating. Tray hide continues through the existing hide path without cleanup.

`DataCenterPanel` applies compact mode to the existing native SQL host and preserves its manual and Agent owners while views change. Its close path requires manual cleanup before workbench/Agent cleanup. The real-host fixture exercises the embedded overflow menu's Format, Clear, and old AI-draft/Object-details action, then verifies the typed result remains visible after the side panel is hidden again.

The Agent's fixed mode explanation and Target value now use a single line sized to their actual label viewport. Qt right-elision adds a visible ellipsis when needed, and each full value remains in its tooltip (`fullCopy` and `fullValueText` properties back the UI). Switching display language/mode or resizing does not rebind the target, controller, snapshot, or event generation.

## Verification

- The six named workspace lifecycle tests plus both application shutdown modules — **17 tests passed**. This covers all-host UNKNOWN/cancel with no action, unknown-commit zero IO, retained refs while pending, tray hide, a one-time original quit chain, legacy worker polling, and action statuses that must not exit.
- Agent panel behavior — **20 tests passed**.
- Windows-QPA Agent layout — **3 tests passed**. The added regression checks Target elision against `fontMetrics().elidedText`, full tooltip preservation, non-overlapping subtitle/context rectangles, and visible composer/Send bounds at 960×640 and 1160×720.
- Production Data Center workspace + native SQL host — **1 test passed**, with zh/en × 960×640/1160×720 subcases. It uses TEMP model/catalog fixtures and patched driver calls that fail if invoked; populates a typed result page (`42`, `19.25`); captures screenshots; clicks Format/Clear/Object-details actions through the real overflow menu; then checks the SQL result view remains in bounds and workspace scroll range is zero.
- `py_compile` of the eight touched Python files and scoped `git diff --check -- main_window.py panels/data_center_panel.py` — **exit 0**.

Exact PowerShell commands:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_workspace.DataCenterWorkspaceTests.test_manual_aggregate_blocks_commit_for_unknown_before_any_host_action tests.test_data_center_workspace.DataCenterWorkspaceTests.test_manual_gate_blocks_new_workspace_and_query_without_disposing_current_host tests.test_data_center_workspace.DataCenterWorkspaceTests.test_unknown_workspace_decision_is_compact_and_warns_rollback_is_not_proof tests.test_data_center_workspace.DataCenterWorkspaceTests.test_workspace_close_cancel_and_pending_cleanup_preserve_owner_until_agent_finishes tests.test_data_center_workspace.DataCenterWorkspaceTests.test_unknown_rollback_close_keeps_unknown_outcome_notice_after_owner_cleanup tests.test_data_center_workspace.DataCenterWorkspaceTests.test_panel_close_event_refuses_unresolved_manual_owner_without_cleanup tests.test_data_center_application_shutdown tests.test_agent_application_shutdown -v
$env:QT_QPA_PLATFORM='offscreen'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_agent_panel -v
$env:QT_QPA_PLATFORM='windows'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_agent_layout -v
$env:QT_QPA_PLATFORM='windows'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_workspace.DataCenterWorkspaceTests.test_actual_default_host_compact_layout_keeps_agent_and_typed_result_reachable -v
.\.venv-build\Scripts\python.exe -m py_compile main_window.py panels/data_center_panel.py ui/data_center/agent_panel.py tests/test_data_center_workspace.py tests/test_data_center_application_shutdown.py tests/test_agent_application_shutdown.py tests/test_data_center_agent_panel.py tests/test_data_center_agent_layout.py
git diff --check -- main_window.py panels/data_center_panel.py
```

Latest Windows-QPA screenshots (the SHA-256 values are in the table):

| Fixture | Screenshot | SHA-256 |
|---|---|---|
| Chinese, 960×640 | `%TEMP%\pengtools-manual-lifecycle\data-center-zh-960x640.png` | `E8ECDF7768FF7E1597D84DE586164C9E5F6DF17702B1DE2EF3863A5893F85F20` |
| Chinese, 1160×720 | `%TEMP%\pengtools-manual-lifecycle\data-center-zh-1160x720.png` | `55368421633AC34A38F6FA1C23AB31F34C15C0C769CFF546B90A289A0837CCAB` |
| English, 960×640 | `%TEMP%\pengtools-manual-lifecycle\data-center-en-960x640.png` | `F2EB40BBA5D08CD336644AB87174680EAA935951E05C82CE4D5FBB0CEE162E71` |
| English, 1160×720 | `%TEMP%\pengtools-manual-lifecycle\data-center-en-1160x720.png` | `06CC5E1CC20F55292B4362A0FF3F7D0C585FA1C1498950E030498F564B969CDC` |

Comparison screenshots from the failed wrapped-height attempt are also preserved beside these files with the `-before-header-fix` suffix. The phase baseline for the Agent panel and its two focused test files is `%TEMP%\pengtools-agent-label-20261002\baseline.json`; image/source hashes and run metadata are in that directory's `results.json`.

## Limits

The lifecycle coordinator tests use injected fake owners and a lightweight `MainWindow` method harness; they do not prove a full running application's exit path. The integration-owned `AiWorkbenchPanel.workbench_shutdown_cleanup()` seam is still pending, so real database/Agent worker cleanup through that host is not yet fully connected or verified. The Windows-QPA screenshots exercise the production workspace and native host with isolated TEMP fixtures and no database/model call; they are not captures of the user's running window. No real database transaction, provider request, or application shutdown was performed.
