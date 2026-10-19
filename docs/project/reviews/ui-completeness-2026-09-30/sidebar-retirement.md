# Native sidebar retirement fix — 2026-09-30

## Baseline and scope

- Repository: `D:\PengTools`, branch `dev/2026-09-23`, baseline HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`.
- The working tree was already broadly dirty. This work changed only `ui/prism_sidebar.py` and added `tests/test_prism_sidebar_retirement.py` plus this report.
- Before editing, the exact dirty `ui/prism_sidebar.py` bytes were copied to TEMP. Its SHA-256 was `0eb5d83fe9665b6bc4303a30e13259598702efd4bb53feb67101305a67140268`. The resulting patch changes only `_clear_layout`; the existing settings `gear`→`settings` icon adaptation is preserved byte-for-byte.

## Change and regression coverage

`_clear_layout` now calls `hide()` on each widget removed from the navigation and footer layouts immediately before its existing `deleteLater()` call. It keeps Qt's deferred destruction and the existing cache clearing/rebuild behavior. It does not synchronously delete widgets, pump events in product code, or change MainWindow/sidebar ownership.

The new behavioral test captures the old layout widgets before each mode, language, and nav-model rebuild. It verifies those widgets are hidden and still alive before `DeferredDelete`, checks replacement controls are visible, and clicks live controls to confirm exactly one expected navigation signal. After sending Qt's normal deferred-delete event, it confirms every retired widget has been destroyed. The nav-model case also checks the existing settings leaf remains mapped to index 7 with a non-null icon while the source model still reports the `gear` role.

The bug was reproduced in Windows-native sidebar captures supplied with the handoff: `C:\Users\Lenovo\AppData\Local\Temp\PengToolsModuleCompletenessAudit-5ff10656895f4af6b40da373e945ef1c\windows-native\nav-7\nav-7-960x640.png` shows old labels/icons overlapping the collapsed rail, while `C:\Users\Lenovo\AppData\Local\Temp\PengToolsModuleCompletenessAudit-5ff10656895f4af6b40da373e945ef1c\windows-drained\nav-7-drained-960x640.png` shows the overlap gone after deferred deletion. This new test asserts the lifecycle property directly; it does not claim to reproduce Windows DPI or screenshot rendering.

## Verification

Command:

```powershell
.venv-build\Scripts\python.exe -m unittest tests.test_prism_sidebar_retirement tests.test_prism_sidebar_menu tests.test_prism_native_sidebar_geometry -v
```

Result: exit code 0; all 8 tests passed in 3.314 seconds. This covers the new regression, existing sidebar leaf/menu/settings behavior, and native sidebar geometry plus its live footer/menu reuse checks.

## TEMP evidence

`C:\Users\Lenovo\AppData\Local\Temp\pengtools-sidebar-retirement-20260930-818fba72-5176e850` contains:

- `prism_sidebar.before.py` and `baseline.sha256` — exact pre-edit dirty source snapshot and its hash.
- `prism_sidebar.after.py`, `after.sha256`, and `prism_sidebar.patch` — resulting bytes, hash, and isolated before/after delta.
- `sidebar-tests.log` — output from the combined passing test command.

No production data, configuration, package assets, or other dirty files were changed.
