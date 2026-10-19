# Shell, dialogs and loading audit — 2026-09-30

Repository: `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`; the working tree is intentionally dirty. This audit uses the V2.1 calm-theme specification and current source. It preserves pre-existing edits and adds no service calls, business-task state machine, or user-data access. The authority sources are [`theme_manager.py`](../../../../ui/theme_manager.py), [`layout_metrics.py`](../../../../ui/layout_metrics.py), and [`navigation_model.py`](../../../../ui/navigation_model.py). The specification's static inventories and previous captures are useful indexes, not complete runtime acceptance.

## Runtime evidence

- Isolated public Qt suites passed: `tests.test_prism_main_shell` 19/19, `tests.test_ui_consistency_buttons` 16/16, `tests.test_prism_titlebar` 8/8, `tests.test_prism_window_frame` 13/13, `tests.test_prism_dialogs` 14/14, `tests.test_quick_panel_lifecycle` 17/17, and `tests.test_prism_native_sidebar_geometry` 1/1 under offscreen Qt. Dialog tests exercise simulated 960×640/font-scale cases and synthetic geometry; these are not physical screen/DPI evidence.
- Startup splash and ThinkingIndicator lifecycle timers passed in a focused Windows-QPA run. Under offscreen Qt those timer-positive assertions fail because [`ui/motion.py`](../../../../ui/motion.py) deliberately disables motion when `QT_QPA_PLATFORM=offscreen`. The offscreen expectation does not establish a product timer regression.
- A focused Windows-QPA `tests.test_ui_motion` run had two lifecycle checks pass and one source-contract failure. `test_dashboard_keyboard_and_demo_row_contract` expects the removed literal `:tabindex="isRowInteractive(r) ? 0 : undefined"` in `frontend/src/dashboard/DashboardApp.vue`; the current app delegates rows to `frontend/src/dashboard/components/MonthlyTasks.vue`, where `isRealTask(task)` and the real-row `role`/`tabindex` handling live. This is a stale `SOURCE_CONTRACT` assertion, not a demonstrated runtime keyboard failure. No compatibility markup or test rewrite was made.
- The one broad scoped UI invocation aborted inside Qt with exit `-1073740791` while running the floating-small-dialog case in `tests.test_quick_panel_lifecycle`; it emitted no unittest summary. Modules were then rerun separately. A fresh Windows-QPA run of `tests.test_prism_native_sidebar_geometry` also aborted with the same native exit; its isolated offscreen run passed 1/1. Treat Windows native-fallback geometry as unresolved runtime evidence, not a confirmed layout defect.
- Windows-QPA WebEngine smoke used the production local `resources/webui/vue/chrome.html`, observed compact/expanded widths 72/248, and opened the native sidebar group menu for requested groups `[2, 0]`. This verifies the local WebEngine/native-menu boundary with a stub/data-only host, not the complete app, user navigation state, or physical fallback renderer.
- A bounded in-memory navigation probe cycled all 21 `NAV_MODEL` leaves 200 times with panel/service creation stubbed; each `_show_panel` transition and sidebar/current-tab selection agreed. It completed in 384.5 ms. This checks shell state/ownership only, not business panels, memory growth, or a historical performance baseline.
- `tests.test_quick_panel_lifecycle` passed 17/17, including the bounded 100 expand/collapse state/timer-ownership probe across tools, learn, and chat modes. It uses synthetic screen geometry; it does not establish real drag/snap behavior, 60 fps, 10-minute CPU use, or multi-monitor/DPI behavior.

TEMP logs retained for review:

- Broad aborted run: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-ui-completeness-180b5d7cf0b74fef9323ec6954c6a839.log`
- Windows-QPA motion/source-contract run: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-motion-windowsqpa-c9a1a443baf442859671ac39e32b4ec5\run.log`
- Windows-QPA native sidebar geometry abort: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-native-fallback-windowsqpa-a1a7b47a0c6f43eca913a62ea8e7a105\run.log`
- WebEngine/native menu smoke: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-shell-webengine-windowsqpa-7a91534d5f1b44efae73a2fa113bec60\probe.py` and `probe.log`
- 200-cycle shell probe: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-shell-nav-200-9fb2ac34a0bd4f3781979a5132c97a11\probe.py` and `probe.log`

## Dialog and window coverage

The existing acceptance matrix enumerates 26 direct `QDialog` subclasses across `panels/` and `ui/` and has initial 13/16-point captures. It explicitly excludes indirect subclasses, platform dialogs, and factories; a capture does not verify save/cancel, validation/error, permissions, keyboard focus, long text, or close behavior. Seven dynamic `QDialog(self)` sites also need interaction acceptance:

| Call site | Current path |
| --- | --- |
| `panels/ai_token_edit.py:164` | `_view_tokens` |
| `panels/ai_workbench_panel.py:1156` | `_view_snapshot` |
| `panels/interface_debug_panel.py:4121` | `_show_history_cleanup_dialog` |
| `panels/interface_debug_panel.py:4243` | `_show_environment_config_dialog` |
| `panels/interface_debug_panel.py:4359` | `_show_url_filter_config_dialog` |
| `panels/requirement_panel.py:427` | `_choose_date` |
| `panels/sql_panel.py:1471` | `_offer_apply_draft` |

The direct-dialog list and its scope are in [`ACCEPTANCE_MATRIX.md`](../../../ui/prism-implementation-2026-09/ACCEPTANCE_MATRIX.md). The isolated dialog contract suite passes, including long confirmation text and simulated small-screen bounds. The frame suite also passes its negative-coordinate/fractional-DPR adapter tests, but it cannot prove actual Snap, multi-monitor transitions, per-monitor DPI changes, transparency composition, or native-window behavior on each target display. CJK visual fit is not claimed from offscreen captures.

## Loading acceptance matrix

The LD targets below are from section 10 of the V2.1 specification. Exactly one primary wait indication is appropriate for each existing async operation; static progress text and existing `ThinkingIndicator` count when they are already visible at the relevant boundary.

| LD | Current source and evidence | Finding |
| --- | --- | --- |
| 01 startup | `StartupSplash` and `tests.test_startup_splash`; focused Windows-QPA timer lifecycle passed. | Implemented path has focused runtime coverage; launch timing/fallback performance baseline is still unmeasured. |
| 02 module/task overlay | `ui/aurora_progress.py`; token, delay, progress, terminal dwell and hide lifecycle are exercised by `tests.test_loading_feedback`, `tests.test_prism_loading`, and the cancellation suite. | Existing public behavior retained. LD08 cancellation rendering was repaired as described below. |
| 03 button busy | `ui/button_busy_ring.py`; `set_button_busy` remains used by `panels/ops_log_panel.py:538,553` for the server editor Test button. Redis/Mongo worker actions now use their result-workspace `QueryStatusBand` as their single primary wait signal. | The busy-ring helper is not broadly wired into every async button; the NoSQL actions have a primary status band and do not need a second spinner. |
| 04 query/refresh band | `ui/query_status_band.py` has three production instances: `panels/ai_workbench_panel.py:625`, `panels/db_redis_panel.py:221`, and `panels/db_mongodb_panel.py:161`. | Redis/Mongo bands are wired to existing worker starts and accepted completion/failure callbacks. Their action and result-preservation evidence is in [`nosql-loading.md`](nosql-loading.md). SQL retains its existing `reset=True` behavior; it was not changed for the status-band target. |
| 05 AI first-response wait | `ui/thinking_indicator.py`; existing uses include `panels/agent_workbench_panel.py:1126`, `panels/model_chat_panel.py:645`, and the floating chat indicator in `ui/quick_panel.py:313`. Focused Windows-QPA lifecycle passed. | Existing wait indication is present on these chat paths; this does not validate real AI service response timing. |
| 06 genuine progress | `ui/data_center/agent_panel.py:994-1008` sets the existing progress bar immediately from a real numeric event (`setValue(int(number))`, then formats the percent). | This is truthful, zero-delay progress with no predicted value. A short visual interpolation is a presentation-smoothing option, not an outstanding completion defect or reason to change worker cadence. |
| 07 floating AI | `ui/quick_panel.py`; existing singleton/mode/timer lifecycle suite passed, including bounded 100-cycle expand/collapse. | Three-mode synthetic lifecycle verified; real placement across physical screens remains pending. |
| 08 terminal cancellation | `AuroraProgress.cancel()` now renders a static neutral stop square/chip, stops the animation timer, reuses the existing fail-linger duration, and advances the existing token generation. Only `AiWorkbenchPanel._cancel_scan` changed its displayed terminal call from `fail` to `cancel`; worker cancellation still sets the existing flag once. | Focused tests cover pending/visible cancel, stale tokens, late success/failure, hide cleanup, English chip, and the production panel callback. No business cancellation contract changed. |

### Redis/Mongo status-band completion and limits

The earlier source audit found missing busy feedback at Redis `_refresh_keys`, `_start_search_scan`, `_load_more_keys`, `_test_connection`, `_load_key_meta`, `_load_key_value`, and `_run_command`, plus Mongo `_refresh_collections`, `_run_query`, `_reset_query`, `_shift_page`, `_load_schema`, `_test_connection`, and `_run_command`. That gap is now addressed with the existing `QueryStatusBand` at each result workspace. Each existing worker start sets the localized running text; accepted terminal callbacks update success/failure text. The band is the primary wait signal for these actions, with no added button spinner, task machine, or worker cancellation behavior.

The isolated fake-worker runtime suite covers those actions, callback ordering/error paths, Redis overlap and stale-callback guards, old visible rows during pending refresh/query work, and existing search/reset/command-input behavior. It blocks Python socket connections and makes no database calls. See [`nosql-loading.md`](nosql-loading.md) for the exact command and evidence. This verifies panel wiring and state presentation; it does not verify Redis/Mongo connectivity, service latency, or live results. Redis search continues its existing list-clearing behavior, and SQL reset behavior is unchanged. The module owner reports that two targeted reruns after the shared QueryStatusBand font fix passed, closing that narrow-width font check; this does not imply live-service or physical-display acceptance.

`AiWorkbenchPanel` retains its existing Aurora wait for ordinary DB work and status-band/cancel path for fetch-all. Chat generation already has stage text and `ThinkingIndicator`.

## LD08 repair verification and delta

`tests.test_loading_cancel_feedback` passes 6/6 standalone. The scoped combined cancellation regression set (`tests.test_loading_cancel_feedback`, `tests.test_prism_loading`, `tests.test_loading_feedback`, and `tests.test_ai_workbench_db_scan`) passes 54/54 in 0.730 s. The production-panel integration runs in a child process whose config paths are redirected before importing the panel; importing the test in the parent leaves shared config constants unchanged. A Windows-QPA font metric probe measured the English `Cancelled` chip text at 53.12 px within its existing 60 px badge.

The exact before/after snapshots and narrow source patch are in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-shell-dialogs-before-b1ab88885cdf41a8b79ad49652a3d0d4` and `C:\Users\Lenovo\AppData\Local\Temp\pengtools-shell-dialogs-after-4b4c57f665864fcba49d812d8715072a`. Baseline hashes were captured from the already-dirty files before this repair; the only `ai_workbench_panel.py` delta against that snapshot is the `_cancel_scan` call. The new test file is additive. `git diff --check` passed for the owned product/test paths.

The latest combined-suite log is `C:\Users\Lenovo\AppData\Local\Temp\pengtools-loading-cancel-verify-cc4c29a6ded445f0a89c5008733b17fc\run.log`. No live DB, SQL, Redis, Mongo, AI, SSH, or production-data operation was run. No full-package performance or physical-display acceptance is claimed: the V2.1 first-interactive baseline, ten-minute idle CPU, actual 60-fps behavior, memory trend over 200 real navigations, physical multi-monitor/Snap/DPI/transparency, reduced-motion OS preference integration, and final visual signoff remain unverified.
