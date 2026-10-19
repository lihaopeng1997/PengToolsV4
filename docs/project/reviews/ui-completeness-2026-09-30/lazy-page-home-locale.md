# Lazy page home-button locale repair — 2026-09-30

## Baseline and scope

- Repository: `D:\PengTools`, branch `dev/2026-09-23`, HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`.
- The working tree was already broadly dirty. Before editing, `main_window.py` was snapshotted to `C:\Users\Lenovo\AppData\Local\Temp\pengtools-lazy-locale-2bc716c3ddc049d0a15a434953a395e6\main_window.before.py` (SHA-256 `F6DE646D11C7A0422B77D01A72A57C986813DCEB51C1C42B9EB918DF5DCE6217`). The saved before-to-after delta is `main_window.delta.patch` in the same TEMP directory (SHA-256 `CABB1EBB8C48EDAFB09984898C108DCB1C03A479987669EAE053EFBCC6C6CE58`).
- The source change is confined to `MainWindow._apply_panel_chrome` in `main_window.py`. Existing navigation callbacks, panel instances, layout handling, and settings persistence were left intact.

## Behavior

With English selected at cold start, entering the lazy Docx route created a real `DocxUpdatePanel` whose header button still said `返回首页`; its tooltip was Chinese too. `GatewayDecodePanel` used the same header construction path. The existing language switch repaired buttons already present, but a panel created later relied on `_apply_panel_chrome`, which did not localize its header button.

After each panel's existing `set_language` call, `_apply_panel_chrome` now applies the current window language to every child named `header-home-btn`, using the same English/Chinese button text and tooltip as the existing language-switch path. This also covers future lazy-created pages without changing their route or callback.

## Verification

Command:

```powershell
.venv-build\Scripts\python.exe -B -m unittest tests.test_lazy_panel_home_locale -v
```

Result: exit code 0; `1` test passed. The test runs in a separate offscreen Qt process with TEMP-remapped config and synthetic English settings. It skips only startup's eager warm-up so the actual production `_show_panel` routes lazily create Docx and Gateway. It checks the live home-button inventory in English, Chinese, and English again; clicking the real Docx home button routes once to index 0 and preserves both panel identities. Tray, hotkey, keep-awake, and WebEngine services are not started. Python socket connections are blocked and none were attempted. Settings-save calls are trapped and none occurred; the TEMP config directory remained empty.

File-backed evidence is in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-lazy-home-locale-00ma1bxr` (`result.json` SHA-256 `22A958D9C972C4399B4FC0D9A74E12A21E238A448324514E3C60457F0E3F6541`). The new regression source SHA-256 is `440D8265A5AC58C00B3882494560EBC9C3DF6FAF5041F76487E40E5BE5FE4909`; current `main_window.py` SHA-256 is `C21AE9A4913683A32A25E040A476386DF80F7E2CE6CFC8307E843C311C43F4E0`.

This is a behavior regression check, not a screenshot or visual acceptance. No package build was run for this final fix; the root task will build a fresh candidate from the updated source.
