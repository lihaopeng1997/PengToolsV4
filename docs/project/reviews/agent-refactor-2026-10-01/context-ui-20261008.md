# Agent context and conflict UI — 2026-10-08

This change adds a read-only entry point for stale Agent runs and a transient status for context planning. The original conversation remains in the normal history. Old conflict results can be inspected without replaying their tools, and context budget events are shown only in the current UI session.

The workbench refreshes a short conflict count from workspace records and shows a button only when entries exist. The button opens a non-modal, scrollable `QPlainTextEdit` viewer. It renders public message roles and content, skips messages carrying `reasoning_content`, omits stored tool-call arguments, and caps displayed text at 256 Ki characters. The viewer has no execute, replay, edit, or delete action. Its initial frame is constrained to the intersection of the embedded panel and active screen work area.

`run_agent_loop` forwards only the fixed scalar shape from `AgentEvent.BUDGET` through a JSON allowlist. The panel validates the schema, exact enums, bounded integer values, and booleans again before display. Tool-call budget events, malformed data, unexpected fields, and private reasoning fields are discarded. The short status says whether the request was shortened while the tooltip explains the local UTF-8 heuristic, the known/unknown provider-window boundary, and that original history remains available. These progress events are not written into messages or workspace records.

The conflict viewer does not alter `_agent_worker` ownership or cancellation. A gated real `QThread` test opens and closes it while the worker is still running, then releases the fake adapter and waits for the existing `finished` path. Another test creates a stale result through the real worker/store flow, reads it through the viewer, and checks that the saved workspace is byte-for-byte equivalent as decoded data before and after viewing.

The targeted verification command was:

```powershell
.\.venv-build\Scripts\python.exe -B -m unittest -v tests.test_agent_workbench_runtime tests.test_agent_workbench_layout
```

All 22 tests passed. The suites patched settings and splitter-layout reads/writes, redirected Agent Store data to temporary directories, replaced catalog access with an empty fixture, and installed failing network/catalog guards; guard hit counts remained zero. The layout test covers Chinese and English at 17 px and 21 px, with 960×640 and 1320×860 embedded sizes, frame containment within parent and screen, read-only behavior, and scrolling of long history.

The before snapshots and exact hashes are in `evidence/agent-context-ui-20261008/before-manifest.json`; final source hashes and the test receipt are in the same evidence directory. `synthetic-context-conflict-ui.png` was captured from an offscreen Qt fixture using only synthetic TEMP data. This runtime reports no installed Qt font families, so glyphs appear as boxes in the image even though text and geometry assertions passed. The screenshot is useful for bounds and control placement only; it does not constitute visual acceptance in the existing app. No existing app window, real catalog, profile, settings, key, network, or model request was accessed during this task.
