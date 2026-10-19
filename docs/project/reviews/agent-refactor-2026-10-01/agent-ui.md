# Data-center Agent UI package

Date: 2026-10-01
Branch and base: `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`

This package places the existing connection-bound Agent widget in the unified Data Center workspace without creating a second controller or model host. At a narrow workspace width, the same Agent host is shown as a drawer; when the existing layout metrics leave room for the 340 px rail and the required SQL area, it is shown beside the native workbench. Closing or reopening the Agent changes visibility only, preserving the host, controller, snapshot, prompt, and displayed evidence.

The Agent presentation now keeps the prompt composer at the bottom while answer, provider-supplied reasoning, tool evidence, SQL, and result preview remain in one scrollable channel area. Channel buffers stay bounded, and incoming text preserves the reader's scroll position when they are not already at the end. Progress remains hidden until an event supplies it. `DataCenterAgentPanel.set_display_context(language, mode)` translates presentation copy for `zh`/`en` and `query`/`draft`; it does not retarget a run or mutate controller state. Draft mode is labeled as not accessing a database.

The native page header now receives the actual language and updates its Home button when the language changes. The reusable Agent widget remains free of database-driver and configuration imports.

## Verification

The targeted command passed all 32 tests:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv-build\Scripts\python.exe -m unittest tests.test_data_center_agent_panel tests.test_data_center_agent_layout tests.test_data_center_workspace -v
```

`py_compile` passed for the Agent panel, Data Center page, and both Agent UI test modules. `git diff --check` passed for the owned source and test files.

Windows QPA screenshots were captured at 13 pt with a temporary connection/model fixture, `WA_DontShowOnScreen`, and database calls patched to fail immediately. No model request or database connection was made. In both 960×640 screenshots, the real production Agent host uses the drawer and the composer is visible at 676×385; in both 1160×720 screenshots, the same host uses the 340 px rail (Agent panel widths are 324 px). The fixture had a draft prompt so the Send button was enabled. Stop remains disabled because no run was started; cancellation behavior is covered by the panel tests. The layout test checks that answer, evidence, SQL, and result channels can each be scrolled into the viewport while the composer stays outside the scroll area.

Screenshots:

- `C:\Users\Lenovo\AppData\Local\Temp\pengtools-dc-ui-20261001\agent-ui-zh-960x640.png`
- `C:\Users\Lenovo\AppData\Local\Temp\pengtools-dc-ui-20261001\agent-ui-en-960x640.png`
- `C:\Users\Lenovo\AppData\Local\Temp\pengtools-dc-ui-20261001\agent-ui-zh-1160x720.png`
- `C:\Users\Lenovo\AppData\Local\Temp\pengtools-dc-ui-20261001\agent-ui-en-1160x720.png`

## Remaining boundary

This package does not wire manual-session dirty/unknown shutdown decisions into the Data Center panel or application exit. The agreed five-state, two-phase manual-host contract remains for the follow-up lifecycle package. The screenshot run also does not verify a live model response or active-run Stop rendering.
