# Dashboard live workflow evidence — 2026-09-30

## Baseline and ownership

- Repository: `D:\PengTools`, branch `dev/2026-09-23`, baseline HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`.
- The checkout already contained broad uncommitted work. This report and [test_dashboard_requirement_live_runtime.py](../../../../tests/test_dashboard_requirement_live_runtime.py) are the only files added for this work package; no other files were edited, staged, or cleaned.
- The test exercises only generated requirements and Qt WebEngine state under the run's temporary directory. It does not read production requirement records or configuration values for its fixtures.

## Production path exercised

The Vue dashboard's real completion and test-point buttons in `frontend/src/dashboard/components/MonthlyTasks.vue` emit `openRequirementSection`. The production bridge routes that request through `HomeBridge.openRequirementSection` in `ui/web_shell.py`, `MainWindow._on_web_open_requirement_section`, and `RequirementPanel.focus_requirement_section`.

For completion, the route opened the actual `RequirementDialog`; the test confirmed the status control had focus, selected the existing `已上线` option, and clicked the dialog's existing Save button with `QTest`. The real `requirement_saved` signal reached `MainWindow._push_dashboard_summary` through the existing connection in `main_window.py`. Before navigating away from the requirement panel, the test asserted that the naturally emitted `summaryChanged` payload contained the exact target with `status=已上线`, `done=true`, and `test_points=0/1`, while the distractor stayed unchanged.

For test points, the same dashboard action focused the target's actual embedded `RequirementPanel.test_points_editor` (`TestPointsEditor`), entered one synthetic point, and clicked its existing Add button. Its production persistence callback wrote the isolated requirement file and emitted `requirements_changed`; the existing MainWindow connection then emitted the natural summary payload. Before returning home, the test asserted the target had `test_points=0/2`, retained `status=已上线` and `done=true`, and the distractor remained unchanged.

The test returns through the existing MainWindow navigation only after both pre-navigation payload checks pass. It then inspects the original WebEngine page and a nonce set in the initial DOM. Both saves emitted one summary before return and zero additional summaries during return. The document nonce and page identity stayed stable, and no explicit bridge push, reload, or JS-triggered refresh was used. In the Vue DOM, the production completed-state presentation renders the target chip as `已完成` / `chip ok`; the persisted status and source summary DTO remain `已上线` with `done=true`.

The dashboard currently routes to the embedded test-point editor above. `TestPointsDialog` is declared in `panels/test_points_editor.py`, but this test does not claim that the dashboard opens that dialog.

## Result

Command:

```powershell
.venv-build\Scripts\python.exe -m unittest tests.test_dashboard_requirement_live_runtime -v
```

The final run passed in 3.567 seconds. The recorded synthetic IDs were `REQ-SYNTH-DASHBOARD-C25D92F52A-TARGET` and `REQ-SYNTH-DASHBOARD-C25D92F52A-DISTRACTOR`. The target persisted as `已上线` with the original point plus `Synthetic saved point C25D92F52A`; the distractor kept status `开发中` and its original point. The final dashboard DOM showed target `0/2` and distractor `0/1`.

Assertions passed for the initial Vue rows, exact target routing, focused completion modal, real save click, persisted status, both natural summary callbacks, test-point persistence, same-page DOM update after each return, stable page identity, and no renderer termination. The WebEngine reported one successful dashboard load. The request interceptor observed no non-local URL attempts; it allowed only `file`, `qrc`, `about`, and `data`. Python `socket.connect` and `connect_ex` were disabled, and the recorded Python socket-attempt list was empty.

## Isolation and evidence

The child process used Windows QPA, set Qt test mode and shared OpenGL contexts before constructing `QApplication`, redirected all application data/config constants and explicit `local_data_dir` calls to TEMP, used temporary WebEngine storage and log paths, and replaced only settings/bootstrap warm-up dependencies. Requirement loading and saving, the Vue page, bridges, dialogs, editor, production signal connections, and summary provider remained real. No production file was modified.

Final-run evidence is in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-dashboard-live-runtime-w_b_soox`:

- `result.json` — assertions, natural signal payloads, final synthetic state, request/load/renderer observations, and screenshot dimensions.
- `runtime.log` — child process output.
- `dashboard-after-live-actions.png` — production MainWindow capture after both saves and return navigation. The window is 1440×900 logical pixels; Windows capture is 2160×1350 physical pixels at 150% scaling.
- `webengine-profile`, `webengine-cache`, `webengine-logs`, and `application-data` — isolated run directories.

The default WebEngine profile reported off-the-record mode and `MemoryHttpCache`; its `cachePath()` was empty, and the configured TEMP cache directory contained no files after shutdown. A `QStandardPaths` cache-location query still resolved to Qt's normal per-user `qttest` location on Windows. The probe did not write there, but this path resolution is recorded as an environment limitation rather than represented as a TEMP redirection. No app-startup policy was changed to force a persistent profile.
