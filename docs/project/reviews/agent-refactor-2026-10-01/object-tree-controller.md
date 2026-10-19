# Object-tree Qt controller adapter

This phase adds an asynchronous Qt adapter around the injected `ObjectTreeService` and target-capture/cleanup callables. The controller does not read profiles, construct provider hosts, or infer node identity from displayed names. Callers provide the public service, a `submit_target_capture`-compatible callable, and a composition-owned cleanup callable.

Future completions cross to the controller through a queued Qt signal. The controller retains capture and request references until their futures are terminal, accepts only the latest request for a target/parent/generation, and validates returned target, parent reference, and generation before publishing results. Cancel marks a request's result for discard before requesting cooperative cancellation, so a native operation that cannot stop may finish without publishing or caching its late result; its future remains tracked until terminal, including during cleanup. Terminal requests prune only their own latest-correlation entry, so an older pending request cannot become current after the newest request finishes.

Last-good pages use an LRU bounded to 64 entries and an estimated 2 MiB of serialized display data. The estimate includes UTF-8 target scope, parent and node identity/display text, both the response cursor and input cursor kept in the cache key, notices, and fixed tuple/index/per-record overhead. Cache hits move to the LRU tail. A single page over the byte limit can still be emitted to the UI but is not retained in the cache. This is a serialization-size estimate, not an exact Python heap measurement.

The focused Qt tests use controlled futures and an injected fake service. Before the fix, two new tests failed by observing `page_ready` and `details_ready` after cancellation returned false while the future remained in progress. The final suite covers exact target capture and Qt-thread delivery, stale captures/target/parent/generation results, late canceled page and details results, latest-ID pruning, LRU refresh and entry bounds, UTF-8 byte accounting for multilingual pages and input cursors, oversized-page emission without caching, pagination, last-good retention on failure, QObject destruction with late completion, and cleanup that remains pending or fails. No production workspace, `DataCenterPanel`, or `MainWindow` wiring is included in this phase.

Verification on branch `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`:

- `QT_QPA_PLATFORM=offscreen .venv-build/Scripts/python.exe -m unittest tests.test_data_center_object_tree_controller -v` — 12 tests passed.
- `.venv-build/Scripts/python.exe -m py_compile ui/data_center/object_tree_controller.py tests/test_data_center_object_tree_controller.py` — passed.
- `git diff --check` — exit code 0; the three phase files are new/untracked and were also checked directly for trailing whitespace.

The pre-edit snapshot for this correction is `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-controller-cancel-20261002-4hbntlvp\baseline.json`; it records branch `dev/2026-09-23`, HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`, and byte copies/hashes of all three owned files.

The service/composition boundary is tested through injected fakes only. Production composition lifecycle, provider drivers, workspace-tree rendering, and full application shutdown remain unverified and belong to later integration work.
