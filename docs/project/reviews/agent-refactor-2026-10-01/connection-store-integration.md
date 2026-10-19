# Connection store product integration

**Scope:** connect the existing connection APIs and their owned GUI save, delete, and reload boundaries to the accepted versioned `ConnectionStore`. This change does not edit the configuration module, secret store, database drivers, application window, or profile host.

## Behavior

`tools.db_connect.load_connections()` now delegates to the strict store reader. A missing file still reads as an empty list without creating directories or writing a file; corrupt, unsupported, or future-format data raises a stable `DbError` code rather than becoming an empty list. The legacy list-returning `save_connections(items)` remains an explicit full-list operation. Callers with a captured revision can pass `expected_revision=`. Legacy callers without one get a revision read immediately before save, which protects the read/commit interval but cannot identify a stale list retained by that caller. Connection upsert and delete use store patch/delete operations directly, so they do not rewrite a stale bulk snapshot.

Partial upserts merge fields into the stored row. Defaults are applied only when creating a row; a sparse edit no longer resets dialect, host, port, database, or username. Omitted, `None`, or empty password input leaves the stored opaque token unchanged. Only a nonempty explicit plaintext password is encrypted, exactly once; the integration tests spy on encryption and decryption and verify zero decryptions. Existing caller signatures and return shapes remain available, with an optional keyword-only `expected_revision` on save, upsert, and delete.

The default workspace-catalog reader accepts legacy raw lists, unversioned `{items: [...]}`, and version 1. It rejects a present non-integer or unsupported `format_version`. The catalog keeps its last-good projection after a read failure and passes the loaded byte revision to default save/delete calls. Existing custom loader/saver callbacks and malformed-row projection remain compatible. GUI connection read/write failures use fixed local messages and preserve the current selection; an AI connection delete removes its local snapshot only after the connection delete succeeds.

The default `DataCenterProfileHost` connection reader now also uses the strict store reader and maps all store failures to `ProfileHostError("PROFILE_UNAVAILABLE")`. It no longer treats a future or corrupt connection document as an empty profile catalog. Its injected loader and model-catalog paths are unchanged. TEMP tests exercise the public capture/load revision path against missing, legacy, unversioned, version-1, corrupt, and future-version documents without writing or contacting a database/model. The legacy `AiWorkbenchPanel` bound reload also catches the stable store error, displays a fixed message, and returns before changing the bound ID/profile/snapshot or rebuilding the view.

## Verification

Integration tests use temporary paths, fake encryption spies, and synthetic opaque tokens/vendor fields only. They cover missing-file read behavior, legacy backup and preservation of unknown envelope/row fields, sparse upsert/default behavior, opaque token handling, exact-revision conflicts, concurrent stale patches, atomic-write failure, corrupt/future formats, workspace last-good state, strict profile-host loading, and fixed error handling in the production panel callback boundaries.

Final focused verification (offscreen Qt):

```powershell
$env:QT_QPA_PLATFORM='offscreen'; .venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_connection_store_integration tests.test_data_center_connection_store tests.test_data_center_workspace tests.test_data_center_workspace_store tests.test_db_connect_redis_mongo tests.test_data_center_profile_host tests.test_model_chat_harness -v
```

Result: 112 tests passed, exit code 0 (1.847 seconds). This final command does not include `tests.test_connection_dialog`.

The final focused test command and result are recorded in the task handoff. `tests.test_connection_dialog` is excluded from final verification: an earlier broad command included its `AiWorkbenchPanel()` construction test before a TEMP-isolation warning was received. That constructor calls the production loader, so that earlier run may have read the configured local connections path; it emitted no file contents and is not used as evidence. No real database connection or model/network call was made for this integration package.

## Baseline and limits

Before editing, byte-for-byte copies and SHA-256 hashes of the six initial existing files in scope were saved under `C:\Users\Lenovo\AppData\Local\Temp\pengtools-connection-store-glue-c901b5fcb3f6482890e1a23e215dbd14`. The later-added `tools/data_center/profile_host.py` was separately captured before its edit at the same baseline root, with SHA-256 `80C6401B1077E715D42113B1A9AE86237F688C2E0C1E4F1DE8FA45DCE23285A8`. The repository was on `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`; the checkout also contained unrelated preserved changes. No Git stage, commit, push, branch switch, or cleanup was performed.

The old bulk API has no revision parameter in existing callers, so it cannot detect that a full-list value was made stale before entering `save_connections`; callers requiring that guarantee must retain and supply the snapshot revision. Per-record upsert/delete and the default workspace catalog do enforce their observed revision and report conflicts without retrying. GUI behavior was exercised with offscreen Qt tests and mocks, not through a manual visual acceptance pass.
