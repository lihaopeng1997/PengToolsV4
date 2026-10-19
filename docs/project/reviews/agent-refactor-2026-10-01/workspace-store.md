# DC-05 SQLite workspace persistence foundation

## Scope and baseline

- Repository: `D:\PengTools`
- Branch: `dev/2026-09-23`
- Base HEAD: `818fba72bf437de9bd49fcd5b198fb1270096a53`
- Requirements: `docs/project/DATA_CENTER_AGENT_REQUIREMENTS.md`, §7.4
- This increment adds a pure-Python store, its isolated tests, and this report. It does not wire a Qt panel, worker, MainWindow, legacy JSON, connection configuration, credentials, or a live session.

## API and stored data

`WorkspaceStore(path)` captures an explicit SQLite file path and performs no I/O in its constructor. `initialize()` or the first read/write opens the file; each operation opens and closes a local SQLite connection. Callers should invoke the synchronous API on a worker thread when used from Qt.

The fixed DTOs are `WorkspaceTarget(tab_id, connection_id, profile_revision, database, schema, tab_kind)`, `DraftRecord`, `HistoryRecord`, and `WorkspaceLayout`. Tab kinds are the explicit safe codes `sql`, `redis`, and `mongo`; text kinds are `sql` and `nosql`. Draft/history records carry only the text, target context, an ISO timestamp, and a short state code; history also returns its persisted favorite flag. The layout stores ordered target references and a selected tab ID. It has no connection, worker, transaction, result, parameter, or auto-execute fields. Unknown stored tab kinds fail with `STORE_RECORD_INVALID`, with no engine fallback.

The store exposes `save_draft` / `load_draft`, `save_history` / `search_history`, `set_favorite` / `list_favorites`, `clear_history`, and `save_layout` / `load_layout`. History search binds user input as a parameter and escapes `%`, `_`, and `\` so they match literally. Clearing history preserves favorites unless `include_favorites=True` is explicit. Layout restoration returns data only; it never connects, executes text, reconstructs a worker, or restores a transaction.

SQLite uses schema version 1 plus an application ID. A newer schema is rejected. A file with another application ID or unrelated existing tables is rejected without migration or modification. Schema creation is transactional. Writes use `BEGIN IMMEDIATE`; any SQLite/storage or budget failure rolls back the write and retains the prior successful snapshot. If SQLite cannot confirm rollback, the API reports `STORAGE_OUTCOME_UNKNOWN` rather than asserting preservation.

## Privacy and budgets

The store accepts only the exact DTO types and fixed scalar fields. It does not accept a profile dictionary, connection URI object, driver/model object, query result, bound-parameter collection, or SSH key. Text is limited to SQL or NoSQL command text. Recognizable credential assignments, `IDENTIFIED BY`, user/role password clauses, credential-bearing URIs, Redis `AUTH`, and private-key markers fail before SQLite is opened, with the fixed `CREDENTIAL_TEXT_REJECTED` code and no input echo. Tests also check that the rejected value does not appear in the database or a WAL file. Ordinary SQL that selects `password` or `token` columns and parameterized predicates such as `token = ?` remain storable; field names alone are not treated as credential values.

Fixed limits are 256 KiB UTF-8 text per record, 500 drafts, 5,000 history rows, 1,000 favorites, 500 layout tabs, 256 search characters, a default search page of 100 (maximum 200), and a 64 MiB SQLite page budget. Capacity errors do not evict or replace successful rows. Text-size, row-count, favorite-count, layout-count, and page-budget checks have focused tests.

Credential scanning is a defensive heuristic for recognizable plaintext forms, not a complete SQL/NoSQL semantic parser. Hosts must pass reviewed non-secret text and metadata DTOs; they must never place credentials in draft/query text or context fields.

## Verification

All tests use temporary SQLite files and synthetic SQL/NoSQL text. They use no network, real database, user data, configuration, credentials, model, driver, or Qt.

| Command | Result |
|---|---|
| `python -m unittest tests.test_data_center_workspace_store -v` | exit 0; 10 tests passed |
| `python -m py_compile tools/data_center/workspace_store.py tests/test_data_center_workspace_store.py` | exit 0 |
| `git diff --check` | exit 0; Git reported only existing LF-to-CRLF notices for unrelated dirty files |
| Focused trailing-whitespace scan of the three files in this increment | exit 0; no trailing spaces or tabs |

Focused regressions include `test_temp_sqlite_draft_roundtrip_restores_only_text_and_context`, `test_history_search_treats_like_wildcards_as_literal_then_favorite_and_clear`, `test_credential_text_is_rejected_before_database_or_wal_creation`, `test_failed_write_transaction_preserves_previous_successful_draft`, `test_fixed_limits_preserve_prior_records_without_eviction`, `test_existing_unowned_sqlite_file_is_not_migrated_or_modified`, `test_nosql_history_and_layout_restore_only_order_and_nonsecret_context` (Mongo → Redis → SQL ordering), and `test_unknown_stored_tab_kind_fails_closed_without_fallback`.

## Integration boundary

This is a storage foundation only. A future UI/worker adapter must explicitly choose its store path, marshal calls off the GUI thread, pass only fixed non-secret DTOs, and decide when to save drafts/history/layout. It must treat `load_draft` and `load_layout` as text/context restoration only and must not infer permission to reconnect or execute. No product panel or application shutdown path is connected in this increment. Real-user persistence location, migration policy, and visual recovery behavior remain for the host integration package.
