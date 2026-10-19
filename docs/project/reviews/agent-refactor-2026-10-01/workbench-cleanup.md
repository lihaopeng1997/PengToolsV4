# Legacy database workbench cleanup

Date: 2026-10-02
Repository: `D:/PengTools`
Branch/base: `dev/2026-09-23` @ `818fba72bf437de9bd49fcd5b198fb1270096a53`

## Change

The SQL, Redis, and Mongo workbench panels now expose `workbench_shutdown_cleanup() -> bool` for the legacy workers they own. Each call cooperatively sets the worker cancellation flag, disconnects result/progress UI callbacks, and returns without waiting or terminating a thread. The method is idempotent. It remains `False` until the panel receives the worker's actual `QThread.finished` signal and releases its per-panel reference; the thread also stays in a module-level keepalive set until that signal. Starting a replacement Redis background scan no longer drops the prior worker's ownership.

The SQL workbench covers both `_DbWorker` and the legacy `_AiWorker`. Its late query/draft outputs are suppressed after cancellation, the AI stage timer stops during workbench cleanup, and `_on_legacy_worker_finished` does not update the closing panel. `on_panel_deactivated()` retains its existing cooperative DB cancellation behavior and does not set the permanent shutdown gate; a regression confirms the panel can run work again after deactivation. Redis and Mongo callbacks now use QObject-bound slots with current-worker/shutdown guards, so a deleted panel is not retained by result lambdas and stale completions do not repaint a closing page.

Manual-session cleanup and Agent cleanup remain separate APIs and are not called by `workbench_shutdown_cleanup()`. The only legacy worker in-flight operations used by the new tests are TEMP fake DB operations. They prove pending-to-finished ownership behavior; they do not prove native database query deadlines, driver cancellation, or cancellation of an already-running mutation. A blocking native call keeps cleanup pending until it returns and the worker closes its connection.

## Verification

All commands ran from `D:/PengTools` with `QT_QPA_PLATFORM=offscreen` and `.venv-build/Scripts/python.exe`, unless noted.

| Command | Result |
| --- | --- |
| `python -m unittest tests.test_data_center_workbench_cleanup -v` | exit 0, 5/5. TEMP Ai DB and draft workers remain pending until finished; Redis replacement retains both workers; Mongo remains pending; deactivation does not gate later work. |
| `python -m unittest tests.test_redis_overview -v` | exit 0, 12/12, including stale-generation callbacks and unhashable `SimpleNamespace` test workers. |
| `python -m unittest tests.test_db_connect_redis_mongo -v` | exit 0, 39/39. |
| `python -m unittest tests.test_data_center_workbench_integration.DataCenterWorkbenchIntegrationTests.test_mysql_production_panel_runs_metadata_query_evidence_and_puts_sql tests.test_data_center_workbench_integration.DataCenterWorkbenchIntegrationTests.test_text_only_nonstream_draft_uses_zero_database_and_keeps_sql_draft_entry tests.test_data_center_workbench_integration.DataCenterWorkbenchIntegrationTests.test_shutdown_cleanup_is_nonblocking_and_clears_only_agent_threads -v` | exit 0, 3/3. |
| `python -m unittest tests.test_query_status_band -v` | exit 0, 1/1 after its teardown fixture began polling the public Agent cleanup API before deleting the panel. |
| `python -m unittest tests.test_nosql_loading_feedback -v` | exit 0, 1/1 after the teardown fixture fix; its isolated child reports no network attempts. |
| `python -m py_compile panels/ai_workbench_panel.py panels/db_redis_panel.py panels/db_mongodb_panel.py tests/test_data_center_workbench_cleanup.py tests/test_query_status_band.py` | exit 0. |
| `git diff --check -- panels/ai_workbench_panel.py panels/db_redis_panel.py panels/db_mongodb_panel.py` | exit 0 for the modified tracked panel sources. |

The first isolated run of `tests.test_nosql_loading_feedback` exited with Windows code `3221226505` and no output. TEMP phase markers located the failure while `app.sendPostedEvents(..., DeferredDelete)` destroyed the separate query-status fixture's panel. At that point its Agent model-catalog thread was still running. The same crash reproduced when the child loaded all three panel source files from the cleanup package's pre-edit TEMP baseline. The fixture previously called `deleteLater()` without polling `agent_shutdown_cleanup()`; it now polls that independent public contract and processes events until the catalog owner finishes, then deletes the panel. The fixture delta is available at `%TEMP%\pengtools-workbench-cleanup-20261002-r1\query_status_fixture_phase_delta.diff`. Its reconstructed pre-edit hash is explicitly not an original byte capture: `%TEMP%\pengtools-workbench-cleanup-20261002-r1\query_status_fixture_phase_delta.json` records `pre_edit_capture: false`, the reconstruction method, and both hashes. This fixture change does not claim to repair the Agent catalog implementation or establish its application-exit behavior.

## Scope and freeze

Correction on the query-status fixture baseline: `tests/test_query_status_band.py` was already untracked (`git status --short -- tests/test_query_status_band.py` showed `??`, and `git cat-file -e HEAD:tests/test_query_status_band.py` failed). No actual pre-edit byte snapshot was saved. The TEMP “before” file/hash is reconstructed by reversing only the added `time` import and `finally` cleanup hunk; the exact phase diff is `%TEMP%\pengtools-workbench-cleanup-20261002-r1\query_status_fixture_phase_delta.diff`, with `pre_edit_capture: false` and reconstruction details in the adjacent JSON. This reconstructed hash is not an original baseline.

This increment changed the three production panel files, added the cleanup regression suite, and changed only the teardown of the existing query-status test fixture. `MainWindow`, `DataCenterPanel`, `AgentPanel`, `ManualSession`, configuration, and credentials were not edited. Existing dirty source and user state were preserved; no staging, commit, or push was performed.

The SHA-256 values at freeze are:

| File | SHA-256 |
| --- | --- |
| `panels/ai_workbench_panel.py` | `4B1352735832EEB71FEEDD404640D67E50CDF5645988D30B3A0272524CCB9ADC` |
| `panels/db_redis_panel.py` | `7D564A6382464BA0BF7E8B4E71D3D7274DB6D2727BE2342D73D6FCA1F5647D31` |
| `panels/db_mongodb_panel.py` | `0799CEF88A586966DC0F4DEA45C38C3F3F1EB4FAB5180281BE4A81E6E0819BC0` |
| `tests/test_data_center_workbench_cleanup.py` | `725519D8BB7E900E9B67D8D915FA4FF52D3A80092DCBF7A3E63BFD5429B9E837` |
| `tests/test_query_status_band.py` | `0A18D4E5F0199F8941BE7BC61BB39896C5BBB5F5EB804AE45D3E16E5F54AA598` |

No live database, native cancellation deadline, or end-to-end real application exit was exercised in this increment. Those checks remain with the shutdown coordinator/driver owners.
