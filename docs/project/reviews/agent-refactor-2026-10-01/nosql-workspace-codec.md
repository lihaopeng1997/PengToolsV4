# NoSQL workspace codec — isolated acceptance

Date: 2026-10-07

## Scope and boundary

This acceptance covers only the pure versioned codec in `tools/data_center/nosql_workspace.py` and its focused tests. It does not connect the codec to Redis or Mongo panels, the application history service, persistence dispatch, or restore UI. No NoSQL product workflow has been integrated or accepted by this report.

The codec imports BSON Extended JSON support and the existing workspace target enum/type and text-size limit. It does not import `ui`, `panels`, configuration, profile resolution, database clients, or network code, and performs no database or filesystem I/O. The stored body keeps exact editor text first and appends a readable Canonical Extended JSON v1 trailer. The full UTF-8 body is bounded by the existing 256 KiB store limit. Unknown versions, providers, actions, fields, malformed EJSON, inconsistent targets, and unsupported parameter types fail with a safe code and copyable display text; failures do not produce runnable defaults.

Snapshots retain only frozen target identity plus the encoded text body. Mongo filter, projection, sort, skip, and limit are serialized at capture time. Sort mapping order is preserved. Each call to `worker_arguments()` decodes a fresh argument tree, so edits to source dictionaries after capture and edits to a returned dictionary cannot change the saved snapshot. Mongo empty Documents queries remain valid captures. Redis bodies keep exact raw text, effective trimmed command text, mode, and database index. Nested parameter strings reject disallowed control characters so Extended JSON cannot bypass the existing store body's control-character check.

Metadata remains plaintext Extended JSON, so the existing WorkspaceStore credential-body validator sees it during `save_history`; the store and schema are unchanged. A focused test submits a synthetic credential-shaped filter through the public store API and verifies the existing safe rejection code.

## Verification

Ran the narrow codec test with the configured bundled environment:

```text
.\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_nosql_workspace -v
Ran 8 tests in 0.027s
OK
EXIT_CODE=0
```

The log is saved at `C:\Users\Lenovo\AppData\Local\Temp\pengtools-nosql-workspace-codec-20261007\unittest.log`. It covers Redis raw/effective text, ordered Mongo parameters and BSON ObjectId/datetime/Decimal128 round trips, source and returned-argument mutation isolation, valid empty Mongo query, invalid types/actions/providers/keys/control characters, damaged and unsupported trailers, target mismatch, body limit, and the existing store credential gate. No tests were skipped. No database, model, or profile was accessed. Visual acceptance is not applicable to this codec-only stage.

## Baseline and protected source integrity

The baseline was captured before any repository writes in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-nosql-workspace-codec-20261007\baseline.json`. The three authorized repository paths were absent at baseline. Branch and HEAD at baseline were `dev/2026-09-23` and `818fba72bf437de9bd49fcd5b198fb1270096a53`.

| Source | Before SHA256 | After SHA256 | Result |
|---|---|---|---|
| `ui/data_center/query_history_service.py` | `856A5962A65C6FBB244D441B9FCC1395B6B892401D777D180ADA647E8B99620E` | `856A5962A65C6FBB244D441B9FCC1395B6B892401D777D180ADA647E8B99620E` | unchanged |
| `tools/data_center/workspace_store.py` | `BD5DA26D0C6BA53E3F536902E794E3E54F22292A65097ED6731C5A43022136D1` | `BD5DA26D0C6BA53E3F536902E794E3E54F22292A65097ED6731C5A43022136D1` | unchanged |
| `panels/db_redis_panel.py` | `C859AC2293C513EA5B0C7DEE66B43328B2C6BB06F08B57AE9C2F15C21F426406` | `C859AC2293C513EA5B0C7DEE66B43328B2C6BB06F08B57AE9C2F15C21F426406` | unchanged |
| `panels/db_mongodb_panel.py` | `A22846F1711A5F9E3FD1C749AB6E17A32CF0150FF7F168649542E6AA07E14A22` | `A22846F1711A5F9E3FD1C749AB6E17A32CF0150FF7F168649542E6AA07E14A22` | unchanged |
| `tools/data_center/profile_host.py` | `C3FBC2F606CA7216C05A1BE48BC2BE13F23D49C66DB47FB65ABE2DFAA1294368` | `C3FBC2F606CA7216C05A1BE48BC2BE13F23D49C66DB47FB65ABE2DFAA1294368` | unchanged |
| `tools/db_mongo_ops.py` (Mongo query parser) | `BC9278104004AE2358823674BA2911F3CB34B73DA12F36BDBBAE9EBE15BC5B6A` | `BC9278104004AE2358823674BA2911F3CB34B73DA12F36BDBBAE9EBE15BC5B6A` | unchanged |
| `tools/sql_guard.py` | `3A9C5DEFE264FAC9B19FEB63E2C553E7BFE0C6D85EA8557142C3064FF826052B` | `3A9C5DEFE264FAC9B19FEB63E2C553E7BFE0C6D85EA8557142C3064FF826052B` | unchanged |

## New source hashes

| Authorized path | Baseline | Final SHA256 |
|---|---|---|
| `tools/data_center/nosql_workspace.py` | absent | `C86E9C1013121A56826101154AC407D1BC1BDC4AFF5C8718C5A16A612179B660` |
| `tests/test_data_center_nosql_workspace.py` | absent | `05EC439B4FE671F35398C6A2B7BF1B01DEA93CF1CF45894FAE9ABF3F0643DACF` |
| `docs/project/reviews/agent-refactor-2026-10-01/nosql-workspace-codec.md` | absent | recorded in final handoff |

The shared worktree contains unrelated pre-existing changes. This stage did not modify them, did not change the workspace store schema, and did not commit or push.
