# Home requirement navigation: constructor-failure guard

Date: 2026-09-30
Branch: `dev/2026-09-23`; baseline: `818fba72bf437de9bd49fcd5b198fb1270096a53`

## Finding

The home create/open callbacks navigated to Requirements and then called the lazy constructor a second time. If the guarded page-navigation attempt returned `False`, that second call could escape the Qt signal callback. The section callback also called the lazy constructor without a guard.

A TEMP-only offscreen probe sent actual `HomeBridge` signals to the three `MainWindow` callbacks with `_show_panel(10)` controlled to return `False` and the ensure callback controlled to raise. Before the repair, create, open, and section subprocesses each exited `-1073740791` (`0xC0000409`) before the signal emission or event loop returned. No user data or application process was used.

## Repair

- Create/open now stop when `_show_panel(10)` fails and use the panel mounted by the successful navigation path. They do not retry panel creation or navigate twice.
- Section catches a panel-construction exception, reports it through the existing status-bar error style, and returns `False` before focusing or navigating.
- Successful create, open, and section actions retain the existing editor/focus behavior. Section still focuses first and switches pages only after focus succeeds.
- The scan worker, result callback, bridge contract, persistence behavior, and exception policy are unchanged.

## Verification

Ran:

```powershell
& 'D:\PengTools\.venv-build\Scripts\python.exe' -B -m unittest tests.test_dashboard_requirement_navigation_failure tests.test_dashboard_requirement_actions tests.test_lazy_panel_home_locale -v
```

Result: **10 tests passed**. The new subprocess tests verify constructor failures across all three real bridge signals, process exit code `0`, event-loop return code `0`, one ensure attempt per failing route, visible failure feedback, and no add/focus/navigation success. The normal-path probe verifies create/open still use the mounted panel and section focus precedes navigation. The existing home-action and cold-start locale tests also pass.

The subprocess probes use synthetic IDs, a synthetic panel, offscreen Qt signals, and temporary config roots with zero files written. They do not launch the packaged application, access a selected user folder, run SVN, or establish the outcome of the separate folder-scan complaint.
