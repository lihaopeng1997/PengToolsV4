# Connection store persistence slice

## Scope and status

This slice adds an injected-path JSON store and TEMP-only tests. It is not wired into `tools/db_connect.py`; the application save/load paths and UI remain unchanged until that integration receives separate approval. No production configuration, database, credential store, Qt, or driver is opened by this module or its tests.

The implementation is on `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`. The only task files are the new `tools/data_center/connection_store.py`, `tests/test_data_center_connection_store.py`, and this report.

## Public contract

`ConnectionStore(path, *, lock_timeout=0.75)` requires a caller-supplied path. `load()` returns a `ConnectionSnapshot` containing stored items, the SHA-256 revision of the exact source bytes (or `None` when no file exists), the format version, and whether an explicit write would upgrade the format. A load does not create the parent directory, lock file, backup, or migrated document. Malformed, oversized, duplicate-key, duplicate-ID, and unsupported-version documents fail with a stable `ConnectionStoreError.code`; they are never treated as an empty list.

Format version 1 is an envelope containing `format_version: 1` and `items`. Historical top-level lists and envelopes with `items` but no `format_version` load without writes; the first explicit mutation upgrades them. Explicit `format_version: null`, invalid versions, and future versions fail closed. Existing envelope fields and retained record fields are copied forward. Missing `group_path` and `environment` remain absent, so display defaults cannot be written back by loading a profile.

`save(items, expected_revision=...)` is the full-list operation: it preserves caller order and explicit ID deletions, requires the revision observed by the caller, and merges fields omitted from each retained ID's record. `upsert(patch, remove_fields=(), expected_revision=...)` applies a patch by record ID, preserving unprovided fields; only named `remove_fields` are removed. If `upsert` omits `expected_revision`, it re-reads the latest document inside the file lock before merging. A supplied stale revision returns `REVISION_CONFLICT` without overwriting the newer file. `delete(connection_id, ...)` removes only that ID and rejects ambiguous duplicate IDs.

The store treats password and token values as opaque JSON values. It neither decrypts, encrypts, nor special-cases an empty string; the later `db_connect.py` adapter must preserve the current “empty password means keep existing token” behavior before calling this store, and encrypt only a newly supplied plaintext password. `profile_revision`, connection ID, OceanBase mode, Redis auth/seeds, Mongo URI/replica-set fields, and unknown vendor fields survive a partial patch when not explicitly changed or removed.

Writes create the parent directory only when needed, acquire a bounded OS file lock (`msvcrt` on Windows, `fcntl.flock` on POSIX), re-read under the lock, verify any supplied revision, and write a same-directory temporary file before `os.replace`. The `.lock` file intentionally remains for later writers; load never creates it. Read and record limits are 16 MiB per document, 10,000 items, 4 MiB per record, 1,024 ID characters, and JSON depth 64. Inputs that exceed a limit are rejected without trimming unknown data.

Before an explicit upgrade from a legacy list or unversioned envelope, the original bytes are saved to `<filename>.pre-v1-<sha256>.bak`. Backup creation is exclusive; an existing mismatched backup is never overwritten. Backup failure prevents publication. Temporary output is removed after pre-commit failures, leaving the source bytes unchanged. If the replace wrapper reports an error after the target already contains the new bytes, the operation reports the committed snapshot rather than claiming a rollback; unreadable post-replace state returns `COMMIT_UNCERTAIN`.

## Verification

`& .venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_connection_store -v` — 19 tests passed, exit 0. The TEMP fixtures cover versioned and legacy reads, display-only missing fields, exact upgrade backups, envelope and vendor-field preservation, full-list order/deletion, patch merge and explicit removal, opaque tokens, concurrent distinct-field patches under the OS lock, stale revisions, bounded lock timeout, unsupported/corrupt/oversized inputs, backup conflict, atomic failure cleanup, and post-commit error handling.

`git diff --check` exits 0. The three new files were additionally checked against empty TEMP baselines with `git diff --no-index --check`; no whitespace diagnostics were reported. The focused test uses only `TemporaryDirectory` fixtures and synthetic encrypted-looking tokens and vendor data. No automated test establishes application integration or visual acceptance.

## Baseline and final hashes

Root independently reviewed the store contracts, lock-held revision/merge, exact-byte upgrade backup, and atomic commit failure handling, then ran the focused module: **19/19**, exit 0 (0.360s), zero skips. Eight owned/dependency hashes stayed stable. Evidence: `C:/Users/Lenovo/AppData/Local/Temp/pengtools-connection-store-baseline-7a05755a70494a34be6f791ee19dcbc7/root-acceptance.json`. This accepts the persistence service in source; product integration is still pending. The report was updated after that evidence snapshot.

Before edits, these three new paths did not exist. Exact copies of the six read-only dependencies were saved at `C:\Users\Lenovo\AppData\Local\Temp\pengtools-connection-store-baseline-7a05755a70494a34be6f791ee19dcbc7`:

- `docs/project/DATA_CENTER_AGENT_REQUIREMENTS.md` — `175558040DFEACAE21D9E5858D9D7D555EBE279DBA8C609A607B9D37B966BD45`
- `tools/db_connect.py` — `F3707956441D187E55229B8F29FCA4FEC4CE77BFD5EC00EE1B9C886371771E2D`
- `tools/data_center/workspace_catalog.py` — `D61B0A6881A1FFF9B726D29806258DF0CB0EEEDEF1F9724C59ED395DAF32AA8D`
- `ui/connection_dialog.py` — `C9769FAF97C76E0C1C3C78F70CE9342E7FEF2D92EBF5352F593BAAA40B0FF844`
- `tests/test_connection_dialog.py` — `D925E2C581A576844FB07117A1F5E4B6C226EA18ADF2D0D6CE64A82E73C80598`
- `tests/test_data_center_workspace.py` — `5801841140A2AD4D24CF8E23091FB275EF67A81D9C05B5E9F47869D7C329A003`

Final SHA-256: `tools/data_center/connection_store.py` — `CE5A547A921F6F4D74516DB34D10EF125582868844D73B9D84B5AAC7797AD314`; `tests/test_data_center_connection_store.py` — `B7586272A72C7706728A34FA293221E857EA065C3A5D9973127243DE79396CC1`. The read-only dependency hashes remain the before values above.
