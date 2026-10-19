# Data Center and application exit joint regression

This checkpoint adds a focused cross-owner regression for application exit. It does not change production source.

The phase baseline was saved at `%TEMP%\pengtools-joint-shutdown-20261002-090536-67b21e330aa6441391d89cdae16f9f53\baseline.json` on branch `dev/2026-09-23`, HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`. The baseline confirms `main_window.py` and `panels/data_center_panel.py` stayed byte-for-byte unchanged in this package.

The test constructs a real `DataCenterPanel` from injected TEMP catalog rows and a fake host factory. It opens only two of four fixture connections; no default host, local connection store, database driver, model request, or user application is touched. Each host exposes the manual and cleanup seams expected by the workspace. One host owns a controlled `QThread`; the other reports immediate completion.

The regression invokes the production `DataCenterPanel.workbench_shutdown_cleanup()` and the production `MainWindow._shutdown()` / `closeEvent()` methods. MainWindow methods run on a light method harness so the test avoids application startup side effects. While the worker remains active, the Data Center panel and workspace references stay retained, the Agent cleanup path does not run, and `quit()` is not called. Once the worker exits, the next nonblocking poll advances through Agent cleanup and the original quit chain exactly once. A separate unknown-session cancellation preserves the workspace without workbench or Agent cleanup; tray hide does not even preflight the hosts.

Focused verification passed: **20 tests**.

```powershell
$env:QT_QPA_PLATFORM='offscreen'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_joint_shutdown tests.test_data_center_application_shutdown tests.test_agent_application_shutdown tests.test_data_center_workspace.DataCenterWorkspaceTests.test_manual_aggregate_blocks_commit_for_unknown_before_any_host_action tests.test_data_center_workspace.DataCenterWorkspaceTests.test_manual_gate_blocks_new_workspace_and_query_without_disposing_current_host tests.test_data_center_workspace.DataCenterWorkspaceTests.test_unknown_workspace_decision_is_compact_and_warns_rollback_is_not_proof tests.test_data_center_workspace.DataCenterWorkspaceTests.test_workspace_close_cancel_and_pending_cleanup_preserve_owner_until_agent_finishes tests.test_data_center_workspace.DataCenterWorkspaceTests.test_unknown_rollback_close_keeps_unknown_outcome_notice_after_owner_cleanup tests.test_data_center_workspace.DataCenterWorkspaceTests.test_panel_close_event_refuses_unresolved_manual_owner_without_cleanup -v
.\.venv-build\Scripts\python.exe -m py_compile tests/test_data_center_joint_shutdown.py
git diff --check -- main_window.py panels/data_center_panel.py
```

The new test also passed a trailing-whitespace scan. The existing MainWindow and DataCenterPanel files still match the saved phase baseline hashes.

This validates the coordinator against TEMP fake workspace hosts and one controlled Qt worker. It does not exercise Ai/Redis/Mongo panels, their cleanup implementations, a full MainWindow construction, a real database, or a complete running-application shutdown. Those boundaries remain separate from this regression.
