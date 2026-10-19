# Redis and MongoDB loading feedback — 2026-09-30

## Baseline and scope

- Repository: `D:\PengTools`, branch `dev/2026-09-23`, baseline HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`.
- The checkout already contained broad uncommitted work. This work changed only `panels/db_redis_panel.py`, `panels/db_mongodb_panel.py`, the previously untracked `ui/query_status_band.py`, and added this report plus `tests/test_nosql_loading_feedback.py`. No files were staged, committed, pushed, checked out, or cleaned.
- The product delta is presentation-only. Both panels now place the existing 32 px `QueryStatusBand` below their connection toolbar. Text is localized for Chinese and English; the shared component keeps reduced-motion feedback static and stops its animation while hidden.
- The shared band now stores full status text separately from its visible label. It elides the displayed label to the current width, recomputes on band or label resize and label font changes, and exposes the full text through `text()`, tooltip, accessible name, and accessible description. Its fixed height and query icon/timer behavior remain unchanged.

## Worker feedback behavior

Redis retains its existing foreground `_worker` and background `_bg_worker` dispatch paths. The new feedback registry stores only the identity, kind, sequence, and display outcome for currently tracked workers; it does not gate or cancel work. A replacement scan removes its already-canceled predecessor from feedback tracking. Completion and failure update feedback only after the existing cancellation and scan-generation guards have accepted the callback. Existing result rendering and error callbacks continue to run as before.

Because Redis permits one foreground and one background task at once, the band shows the newest still-running task. If the newer task finishes first, the older running task remains visible until it finishes; after both finish, the band retains the terminal result from the most recently started task. Existing worker `finished` signals remove their entries, and panel destruction clears the remaining presentation references. No completion history or worker wrapper is retained in the terminal display state.

MongoDB continues to use its existing single-worker guard and direct `completed` / `failed` connections. Those existing panel slots identify the emitting worker with Qt's `sender()` and update the band only when the sender is still the panel's current `_worker`. Busy labels are set only after a real existing action reaches `_run_worker`; invalid input and current-worker guard returns do not fabricate a task state.

The exercised actions were Redis connection test, key refresh/bootstrap, search scan, load more, key metadata, key value, and read-only command; MongoDB connection test, collection refresh, schema load, query, reset, page shift, and read-only command. The regression also checks Redis replacement cancellation, both foreground/background completion orders, Mongo and Redis errors, prior rows while requests are pending, and localized terminal text.

## Verification

Command:

```powershell
.venv-build\Scripts\python.exe -B -m unittest tests.test_nosql_loading_feedback -v
```

Result: exit code 0; `1` test passed. The test launches a separate `-B` Python process, sets Windows-compatible Qt offscreen mode and test paths, remaps application config paths to that run's TEMP directory, blocks every Python socket `connect`, `connect_ex`, and `create_connection`, and uses fake `QThread` workers. It constructs the real Redis and MongoDB panels and invokes their actual action, completion, failure, and Qt signal paths. The fake workers do not run database operations. The recorded Python socket-attempt list was empty.

The passing assertions cover one dispatch per valid action, busy text before completion, result preservation until callbacks, existing reset/search/command-input behavior, callback success/error behavior, Redis overlap recovery in both completion orders, stale canceled callback handling, reduced-motion static status, non-collapsed result workspaces, and feedback-reference cleanup after finish and panel destruction. The existing `tests.test_query_status_band` AI-workbench regression also passed inside the isolated child process using TEMP config. A long synthetic English failure was checked at 16 pt through 1440 px, 960 px, and 560 px widths: the visible label elides to fit inside the fixed 32 px band while the complete error remains in the public text API, tooltip, and accessibility fields, and pending result data stays unchanged. A separate fixed-geometry check changes the inherited font 12→16→12 at 960×32 without another `setText`; the label's settled geometry stays constant and its visible elision follows the new font metrics.

## Evidence and limits

The final isolated run is in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-nosql-loading-15by3zni`:

- `result.json` — actions exercised, both Redis completion orders, config path, and zero socket attempts.
- `test-process.log` — unittest child process output.
- `config` — isolated config root; no production connection list or user config was used.

The final `result.json` SHA-256 is `6BF7E5118FD8E0338C6DAE5F7C98EC3A4FE561376CA7A7C7DAEF2F199A8004E9`. Current product-source SHA-256 values are `56A22C75A5345B215E6CA93B3093622BD2F52CDF730CC2022BEA7A5C0E675C03` for `db_redis_panel.py`, `F48B773C6E4986F5BE23B7C1BBE753ED07941424828E217B82A4C9CAD9051412` for `db_mongodb_panel.py`, and `B94D8470CC3C450C746060CD399187240D02AF4296E2CE0F9788A4802573DA02` for `query_status_band.py`. The corresponding delta-patch SHA-256 values are `E9EF2507DDB8A26014C23880350221111A13150A81D3ABEC6C9D7AEF7732852F`, `ED12D48DC276298B9CC8DD99AE30BA87472E36855B2B527E37A0D606601C4AF1`, and `10E18B2D82783687B6A028880C997D605EC776521056EBE1298E2CF5A0773728` in the same file order.

Pre-edit snapshots and unified delta patches remain in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-nosql-loading-20260930-818fba72-461ac5bf`. Original SHA-256 hashes were `24FD643AD26B9EBAFFFF1904344BC9EC5D8496F22323A4109C826932FB3D8E6F` for `db_redis_panel.before.py`, `C9CFAFEA6BE5B0546067AAC89ED97E9A01BA48D05C484F82A2EE1AB3EFA157A3` for `db_mongodb_panel.before.py`, and `6517A4742D218E5B2E1DD8A96A56CC6F3A710E98CF377408BF6E96CA48A124B8` for `query_status_band.before.py`. The corresponding delta files are `db_redis_panel.delta.patch`, `db_mongodb_panel.delta.patch`, and `query_status_band.delta.patch`.

This verifies real panel composition and callback boundaries with synthetic worker results, not connectivity to Redis/MongoDB or service latency. No screenshot or manual visual acceptance was produced; user visual review remains separate.
