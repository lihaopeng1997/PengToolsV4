# Manual result export UI

Status: UI package accepted by root in source on October 4. This report covers the product UI attached to `AiWorkbenchPanel`; it does not mark the larger DC00–DC06 objective complete.

Branch and base: `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`.

## Behavior

The SQL workbench exposes **Export query results** from its existing More menu. The dialog offers selected rows, a loaded page, and the complete query in CSV, JSON, or Excel format. Selected values come from the typed model's `UserRole`; page and selected-row requests copy immutable snapshots on the GUI thread and write them in a retained `QThread`. A complete-query request goes through the captured `ManualSession` and its original owner cursor.

Before starting a request, the host checks the captured session target and profile revision, query/tab/generation, loaded page chain, and current visible model. Replacing the visible result while the dialog is open rejects its stale snapshot. A running full-query export stays bound to its originating tab; Stop remains available after switching tabs and names the other-tab export in the visible status line. The source owner and tab remain retained while its real driver call runs, and a tab close is refused until that operation ends. Canceling application close preserves the export and transaction state.

The preview shows the captured connection and database. It adds a schema only when that value differs from the database, avoiding a duplicate MySQL label while retaining a distinct Oracle owner. Scope labels and previews use user-facing language and do not display query, tab, generation, or internal state identifiers. English page counts use singular labels when appropriate.

The host keeps the last successful page after a later query fails, so that page can still be exported. Declining an overwrite leaves the existing file unchanged. A running full-query stop waits for the owner call and publishes no cancelled partial file.

## Verification

All database interactions used the production `AiWorkbenchPanel` and `ManualQueryHost` with a fake DBAPI connection. Files were written under `TemporaryDirectory`; no real database, model, user configuration, or user data was used.

- Before UI edits, `.venv-build\Scripts\python.exe -B -m unittest tests.test_data_center_result_export_ui -v` passed **9/9**, exit 0.
- After the behavioral fixes, the focused UI module passed **12/12**, exit 0, with the same command. It covers typed selected rows, a page chooser, XLSX metadata readback, same-cursor full export without SQL replay, last-good results after query failure, overwrite decline, dialog result replacement, tab switching, close and Stop behavior, and safe user-facing previews.
- The combined command `.venv-build\Scripts\python.exe -B -m unittest tests.test_data_center_result_export_ui tests.test_data_center_manual_result_export tests.test_data_center_manual_session tests.test_data_center_result_export -v` passed **54/54**, exit 0, before the final display-only wording/schema-label adjustments and the additional preview/status assertions. The final UI module passed 12/12 after those changes. Root independently accepted the frozen owner and writer suites; the final backend dependency hashes are recorded below.

## Visual review

Root independently ran the final source after removing driver/owner implementation terms from the two Stop messages: `.venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_result_export_ui tests.test_data_center_joint_shutdown -v`, Windows QPA, **15/15**, exit 0 (1.577s), zero skips. The 13 source/dependency/test/report hashes were stable across that run. Root reviewed the real before-file diffs and all four captures below. Evidence: `C:/Users/Lenovo/AppData/Local/Temp/pengtools-result-export-ui-1f26aff09019423698aaed07645dcb95/root-acceptance.json`. The report was then updated with this acceptance. The ordinary `git diff --check` returned 0; a separate check with `core.autocrlf=false` interpreted existing CRLF bytes as trailing whitespace and is not the passing check.

Four Windows QPA captures used the real `ThemeManager` calm theme with `font_size=13` CSS px and the current production panel fixture. The actual primary screen reported DPR 1.5 and logical geometry 1707×1067; the application font measured 9 pt and the export dialog font 13 px. The parent workbench fixtures were 960×640 and 1160×720 logical pixels. The export dialog measured 410×360 logical pixels (615×540 physical); every displayed control fit within its bounds. The 960 captures show the loaded-page scope; the 1160 captures show the full-query scope. These are dialog captures, not a full application screenshot or packaged-runtime acceptance.

| Case | Screenshot |
| --- | --- |
| Chinese, 960×640 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-ui-1f26aff09019423698aaed07645dcb95\screenshots\result-export-zh-960x640.png` |
| Chinese, 1160×720 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-ui-1f26aff09019423698aaed07645dcb95\screenshots\result-export-zh-1160x720.png` |
| English, 960×640 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-ui-1f26aff09019423698aaed07645dcb95\screenshots\result-export-en-960x640.png` |
| English, 1160×720 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-ui-1f26aff09019423698aaed07645dcb95\screenshots\result-export-en-1160x720.png` |

Control geometry, theme/font measurements, viewport sizes, and preview text are in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-ui-1f26aff09019423698aaed07645dcb95\screenshots\metrics.json`.

## Baseline and frozen hashes

Before editing, exact copies and SHA-256 hashes of the three existing owned files and hashes of the initial UI dependencies were saved in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-ui-1f26aff09019423698aaed07645dcb95\baseline.json`.

| Owned file | Initial SHA-256 | Final SHA-256 |
| --- | --- | --- |
| `ui/data_center/manual_query.py` | `CCDE7EF10FAAA7DDE2FA82B0EC464E1AD76E2F793751BD564F27579C29E8AEEB` | `F8E5CCBB5364D7EFDE483091295FD58D206D639291E527E527B6E07DD7E722B0` |
| `ui/data_center/result_export.py` | `B7DB3357AA98B3F95F66D1458BF34CE200E522D5A29C296E1BAEDDE88F2921DD` | `FA15A5F20B9E061AE64FC4F958AC7ACCFEFCCA3D50CF230773C675EDF75D0EB3` |
| `tests/test_data_center_result_export_ui.py` | `08154783847192DF9852AA39905A00B6442C95F80D79B09FD36D98FE698EDD82` | `8866D8706B060BBDC863BD81F547C70FE577667FB0C8A8899F7AE4AEFCAF5312` |

`tools/data_center/manual_session.py` and its focused test changed in the concurrent owner-repair package after this UI baseline, then froze before the final UI/backend run. They were not edited in this package.

| Frozen dependency | Final SHA-256 |
| --- | --- |
| `tools/data_center/manual_session.py` | `4DD5EBFD01F0031664254F5FBDA99BF3A20BCC092A0FCE2415103647F2E56987` |
| `tools/data_center/manual_result_export.py` | `D6D8705E99ED469C2BB2055551F7DA6F1E34A0FF96F5BE0646DB0213065F72D9` |
| `tools/data_center/result_export.py` | `B2E6E4A3721A093134D4BB3E9E9EE5AAC568B95834EDC1754C3A6129C59541FD` |
| `tools/data_center/manual_driver.py` | `620CFF5866269452B87A79153689BAE8F79BE726B0545F7653AC0BFB7D013575` |
| `ui/data_center/result_model.py` | `CBFAE113A5861EA7E49D8E265CC707DB3B7DC345753C89F357F871A59D0B9C30` |
| `tests/test_data_center_manual_result_export.py` | `70BA8C58E0F671AEA2B58E9052711A8764297066D03D1BE3AAA2DC480C088BA1` |
| `tests/test_data_center_manual_session.py` | `66F4AD9DDC0856C0744CDD9F8BCD1F9441057773F92AFC7C0A092C90F88289D3` |
| `tests/test_data_center_result_export.py` | `AC07A5F8FE4CB1CE039E78C7E8E77FCB3919718056A4C4D2478CFE1AED6E5EC2` |

The fake DBAPI evidence does not validate production driver cancellation, a real database, or the packaged application. Those remain outside this UI package's verification boundary.
