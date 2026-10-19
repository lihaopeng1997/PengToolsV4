# Query history UI slice — 2026-10-06

The original 2026-10-06 UI slice added a reusable PyQt query-history and favorites browser backed by the public `WorkspaceStore` API. Its phase-start snapshot at `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-20261006/phase-baseline.json` recorded all three then-owned paths as absent. The branch is `dev/2026-09-23` at HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`; the pre-existing dirty worktree was preserved. The metadata follow-up at the end of this report records the later narrow display delta and current hashes.

`QueryHistoryController` serializes public store operations on one Qt worker thread and caps outstanding requests at 16. Its constructor does no SQLite work. The injected store is borrowed: shutdown stops the worker, reports completion through a future and signal, and never closes the store. The cleanup future is moved to the running state before shutdown is published, so `cancel()` cannot report completion while a store operation or worker thread is still active. It becomes done only after the actual `QThread.finished` callback. A blocked-operation regression verifies `cancel() == False` and `done() == False` while the owner is running, followed by a completed result after owner exit.

The UI supports text and connection filtering, a bounded result limit, favorites, and separately confirmed ordinary-history and clear-all actions. Store failures surface only allowlisted error codes and localized status text. The browser displays each captured target, timestamp, text kind, state, favorite state, and the complete saved SQL or NoSQL text in a bounded, scrollable detail view. The metadata follow-up adds localized row-count and compact evidence-summary details, with the complete bounded evidence tuple available in the tooltip. Target values remain available in the full tooltip. Restore emits the original frozen `HistoryRecord` through `open_as_new_draft_requested`; the slice has no SQL execution, connection-opening, or transaction callback. A caller can save history only by passing a captured immutable record to `save_history`.

The pre-lifecycle-fix copies and hashes are retained in `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-20261006/pre-lifecycle-fix-20261006/pre-lifecycle-fix.json`. They preserve the frozen source, test, and report before the Future repair.

Both post-fix runs passed all eight tests with zero skips:

| QPA | Command result | Log |
| --- | --- | --- |
| `offscreen` | 8 tests, 0.548 s, exit 0 | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-20261006-postfix-offscreen-final.log` (SHA-256 `E005BB2A3CB12C17E7323BD8604F5CA6A48B597FB37250FECC511B1F90DA13A0`) |
| `windows` | 8 tests, 1.056 s, exit 0; the fixture asserted `QApplication.platformName() == "windows"` | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-20261006-windows-qpa-final.log` (SHA-256 `CD3242E541E142C30B9A4CFCDCC88BB959C989F288B95A2AE58274CED33A5D92`) |

The original Windows-QPA fixture registered the installed Segoe UI and Microsoft YaHei fonts and captured all four requested logical widget sizes. At the machine's 150% device scale, PNGs are 1440×960 for 960×640 logical size and 1740×1080 for 1160×720 logical size. Those historical images were inspected for visible controls, target details, and bounded scrolling, but they did not measure effective child-control font sizes; the metadata follow-up below supersedes any inference about 13/16 pt child fonts from those filenames.

| Fixture | PNG size | Capture | SHA-256 |
| --- | ---: | --- | --- |
| Chinese, 960×640 logical, 13 pt | 1440×960 | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-de894ab058/query-history-zh-960-font13.png` | `D3EF55614D5420E8BEB91C1DEAEF434DBA551FE2BE336EE633F1C4362AF1F123` |
| English, 960×640 logical, 13 pt | 1440×960 | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-de894ab058/query-history-en-960-font13.png` | `840BC8D25501A01582958074A7DD1B1A28D8CB88F4A00F03E47130C352C14560` |
| Chinese, 1160×720 logical, 16 pt | 1740×1080 | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-de894ab058/query-history-zh-1160-font16.png` | `890EA3ED12DD33BB78080A8FE0A413E4337534CE2B9D0F3B8BC6D6D3600FB823` |
| English, 1160×720 logical, 16 pt | 1740×1080 | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-de894ab058/query-history-en-1160-font16.png` | `169F46915C782A4BE65C36F6F1AFC70D1288223C915F303E0925A090A63C5324` |

This remains a reusable component slice: product entry-point wiring and the host's shutdown integration are pending. The first UI-helper hashes below describe that earlier freeze and are retained as historical evidence; use the metadata follow-up table and `history-metadata.md` for current source hashes and store behavior.

Historical first UI-helper freeze SHA-256 values (before the metadata follow-up):

| File | SHA-256 |
| --- | --- |
| `ui/data_center/query_history.py` | `4CDFD40E0D212CF9B9D8C2A14F2805108A90F5CD72E43DCF4057BE79BB31B001` |
| `tests/test_data_center_query_history_ui.py` | `9201E8AC2BD1FC3C70FCDDCA517A359CC569CC389A25744030D04EED56E5568F` |
| `tools/data_center/workspace_store.py` (read-only dependency) | `DAC552F8B38DCA7F25B9A85CD18DD6431D1962819BDBD3A9E3BB3FA20AB6AC8D` |
| `ui/theme_manager.py` (read-only dependency) | `61BF45EC56060120C0064FF7EFA0ABD49DC3C55F26214BADAAAAC493B2DE3DA9` |
| `ui/layout_metrics.py` (read-only dependency) | `729E035DB92E6BB819A77B321017A610E55DE249EA29464A25B28E1FB754D0B1` |

## Metadata display follow-up — 2026-10-06

The separate history-metadata increment extends the public frozen `HistoryRecord` with an optional row count and a tuple of fixed evidence-summary DTOs. It displays unknown, zero, or numeric row counts and bilingual tool/state/count/truncation summaries. The list preview shows at most two evidence entries; the metadata tooltip carries the complete bounded summary. SQL/NoSQL restoration still emits the exact record and does not execute or connect. Persistence and migration details are in [`history-metadata.md`](history-metadata.md).

The narrow Windows-QPA fixture applies the existing `ThemeManager.apply(app, "calm", font_size=...)` global application stylesheet before constructing each browser, then lets the browser apply its own calm token stylesheet. It asserts the effective pixel size on the search button, metadata label, SQL details, and restore button. At DPR 1.5, the 17 px and 21 px global font settings resolved to 12.75 pt / 17 px and 15.75 pt / 21 px; `QFontInfo` reported `Microsoft YaHei UI` for each measured control. Segoe UI and Microsoft YaHei application-font families registered, and `QApplication.platformName()` was `windows`. The four 960×640 and 1160×720 logical captures show the row-count/evidence display and reachable controls; all were visually inspected:

| Fixture | Logical / physical size | Effective control font | Capture | SHA-256 |
| --- | --- | --- | --- | --- |
| Chinese, 960×640 | 1440×960 at DPR 1.5 | 12.75 pt / 17 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-zh-960-font13.png` | `2D810232D3F3B463B6F3B28D7B03663D928C350D2ACBA32EE57E6BD0559E596D` |
| English, 960×640 | 1440×960 at DPR 1.5 | 12.75 pt / 17 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-en-960-font13.png` | `05C927F97BDCC1B502076E7A1EEAAF9BE1BC6453F1F612290693E4B131BEDDF3` |
| Chinese, 1160×720 | 1740×1080 at DPR 1.5 | 15.75 pt / 21 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-zh-1160-font16.png` | `EB331C67C16112A4CDA665A94A35D7E5AADB15068638852F942FB982A5520A18` |
| English, 1160×720 | 1740×1080 at DPR 1.5 | 15.75 pt / 21 px | `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/query-history-en-1160-font16.png` | `FE509E4C20A5E9120691809038CCAB61501D812F6C4FEF69FF30966DB18B106B` |

The corresponding machine-readable control/font/capture facts are in `C:/Users/Lenovo/AppData/Local/Temp/pengtools-query-history-ui-captures-20261006-windows-472e25740d/capture-manifest.json` (SHA-256 `E98C050E60F4B3BA1A68B5035AEF84A72D114121AB1A955138C2D508996F03CE`). The current globally themed Windows-QPA UI suite passed 9 tests with zero skips in 2.503 s; its log is `C:/Users/Lenovo/AppData/Local/Temp/pengtools-history-metadata-ui-windows-20261006.log` (SHA-256 `86268FBDF07B3F591D1321F28454AA19032C5EA32BE40102BA954BA509F453BE`). Product entry-point wiring and host shutdown aggregation remain outside this reusable-component increment.
