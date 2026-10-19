# Object tree composition

## Scope

This packet records the pure-Python composition seam between the existing relational/NoSQL profile hosts and `ObjectTreeService`, including the follow-up local Oracle service-scope capture. It does not connect panels or start a database session during construction. Profile capture remains local and credential-free; directory connections are opened only after tree operations are requested.

The checkout remained on `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`; the pre-existing dirty tree was preserved.

## Public API

- `ObjectTreeComposition(*, relational_host=None, nosql_host=None)` creates the lazy production hosts by default and exposes the shared directory service through `.service`. Construction resolves no profile and opens no database/client connection.
- `submit_target_capture(connection_id, *, database_scope="", redis_prefix=None)` returns `Future[TreeTarget]`. Capture runs on one worker with at most 16 pending/running captures. It reads only the selected host's public profile snapshot and uses its fixed connection ID, revision, provider, and mode.
- MySQL and OceanBase MySQL accept an empty `database_scope` as the whole-server root; the object-tree resolver signs each returned database child and its descendant nodes. A later manual table operation may use only that exact signed nonempty database. A supplied MySQL scope remains unchanged. For Oracle and OceanBase Oracle, the host re-loads the selected profile at the captured revision and reads the service using the direct connector's first-present `database`, then `schema` rule. An explicit scope is accepted only if it exactly matches that service; an empty or stale service fails before secret resolution or a connection. Python Oracle retains its connector's existing service-name character check; OceanBase Oracle does not inherit that narrower regex because its direct ODBC connector requires only a nonempty escaped database value. MongoDB defaults to the profile snapshot's database; Redis requires an explicit prefix; `""` represents an explicitly selected whole-keyspace prefix.
- Host routing falls through only for typed, explicit not-found/unsupported errors. Profile drift, invalid profiles, and unexpected loader failures fail closed and do not probe the other host. Errors are reduced to safe public codes.
- `close()` returns an idempotent `Future[CleanupResult]`, seals the composition, requests capture cancellation, and waits for profile-capture workers and the tree service's owner cleanup to actually finish. It does not terminate a blocked loader. Future completion and its callbacks occur outside the cancellation-token lock.

## Verification

`python -m unittest tests.test_data_center_object_tree_composition -v` passed **10/10** tests. The synthetic production-host path covers lazy construction, empty-root MySQL enumeration, revision-checked Oracle service capture and explicit matching/mismatch, Oracle profile-revision races and empty service, OceanBase Oracle ODBC service values, Mongo default/explicit database context, explicit Redis prefix, stale profile rejection, loader failure without cross-host fallback, queue limits, cancellation, pending close, and callback-triggered close. The companion manual table-scope regression then follows actual MySQL table/view and Oracle schema/table nodes through `BoundManualTableScope`, the production provider factory, and `ManualSession.open_table_source`.

The callback-triggered close regression runs in a subprocess with a six-second timeout, so a lock/callback deadlock cannot strand the test process. Fake connectors and clients only were used; no network, real database, user configuration, or credentials were read. `python -m py_compile tools/data_center/object_tree_composition.py tests/test_data_center_object_tree_composition.py` exited 0.

The original composition packet baseline was `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-composition-20261002-3c0dcba046124ab08165c5cdbcd1c875`. For the authorized Oracle service follow-up, before-copies and hashes were saved under `C:\Users\Lenovo\AppData\Local\Temp\pengtools-oracle-tree-scope-20261004\baseline.json`.

The current capture and test source hashes are SHA-256:

| File | SHA-256 |
| --- | --- |
| `tools/data_center/object_tree_composition.py` | pending final freeze |
| `tools/data_center/host_relational.py` | pending final freeze |
| `tests/test_data_center_object_tree_composition.py` | pending final freeze |

The untouched dependencies for the service-capture follow-up are `host_nosql.py`, `object_tree.py`, `profile_host.py`, and the read-only connector/adapter implementation. Their final hashes will be in the stage freeze receipt. The new `host_relational.py` method intentionally uses the existing private `_canonical_profile(..., expected_revision=...)` validation seam and does not add database/model data to `RelationalProfileSnapshot`.

## Limits

All database/client operations were exercised with synthetic fakes. This verifies public host wiring and lifecycle behavior, not native driver compatibility or a real database. At the time of this original composition packet, the seam had not yet been connected to the UI. The object-tree UI work has since been accepted. The manual table/view opening route covered by the later table-source package is still pending UI integration and visual acceptance. The existing object-tree capability notice for the unwired Dameng directory path remains unchanged.

## 2026-10-06 backend resume freeze

The nine files assigned to this backend slice were copied with SHA-256 values before any resume edits at `C:\Users\Lenovo\AppData\Local\Temp\pengtools-backend-resume-20261006`; see `resume-baseline.json`. The root reviewer checked the older Oct. 4 Oracle and root-scope evidence directories cited by historical notes; they are empty, including the expected `baseline.json`. Those historical values are not byte-level before evidence, and no substitute artifacts were fabricated. This resume updated reports only; source and test files match the Oct. 6 copies.

The final focused command was `.\.venv-build\Scripts\python.exe -B -m unittest tests.test_data_center_object_tree_composition tests.test_data_center_table_source tests.test_data_center_manual_table_scope tests.test_data_center_host_relational -v` — **49 tests passed, exit 0**. Full output: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-backend-scope-final-20261006-0736f4cafe0842019d7e77d2e55d6ec5.log`. The composition tests use fakes only and verify revision-checked local Oracle service capture, explicit scope mismatch, an empty direct service, and OceanBase Oracle ODBC service values; companion route tests exercise the production manual-session/provider path. No native driver, real database, or UI visual check was used.

Final hashes for all nine owned files and their dependencies are in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-backend-resume-20261006\freeze-manifest.json`.
