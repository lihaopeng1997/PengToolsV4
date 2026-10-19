# Typed result export backend

## API and boundary

`tools.data_center.result_export.write_result_export()` accepts an explicit `ExportTarget(query_id, tab_id, generation)` and one of three scopes:

- `selected` and `current_page` take an immutable `ExportSnapshot` containing column names and typed rows. A current-page snapshot also carries its page number.
- `full_query` takes a lazy `Iterable[ManualQueryPage]`. It requires page 1, consecutive page numbers, matching query/tab/generation and complete `ManualColumn` metadata (including type code, precision, scale, and nullability), no budget truncation, and an explicit terminal page with `has_more=False`. It checks one item after the terminal page and rejects trailing pages. Duplicate column names remain valid when their metadata is consistent.

The writer is synchronous, pure Python, and has no Qt, UI, model, or database connection dependency. The UI caller must capture selected/current-page data on the GUI thread from `ResultTableModel.pages` or `data(index, Qt.UserRole)`, then call the writer from a worker. A future owner-side bridge can provide pages lazily for full-query export; that bridge is not part of this package. The writer checks cancellation between rows/pages and immediately before publication. It cannot interrupt a blocking page iterator, so its caller must own that lifecycle.

## Versioned value contracts

JSON uses top-level `contract="pengtools.typed-result-export"`, `version=1`, ordered `columns`, and array-of-arrays `rows`; this retains duplicate column names. Each cell is a typed object. CSV keeps its normal header and row shape; special values use a `~PTX-CELL/1~` plus base64url(JSON typed-cell) marker. Ordinary strings beginning with the marker are escaped as typed strings, and formula-like headers/cells are marker-encoded so spreadsheet import cannot execute them. `decode_csv_cell()` and `decode_typed_cell()` decode these values.

XLSX has a visible `Data` sheet and a `veryHidden` `_PT_EXPORT_V1` metadata sheet, which also records the scope and target. Each cell's exact typed value is recorded in the sidecar; long payloads are split into chunks of at most 30,000 UTF-16 code units. Visible cells are conveniences only. All strings, including those beginning with `=`, are explicitly written as string cells. Text beyond Excel's 32,767 UTF-16-unit cell limit is represented visibly by a notice and preserved fully in metadata; numeric/date/byte values that Excel would coerce are similarly recoverable from typed metadata.

One v1 typed-cell example: `None -> {"t":"null"}; '' -> {"t":"str","v":""}; Decimal("1.20") -> {"t":"decimal","v":"1.20"}; 9007199254740993 -> {"t":"int","v":"9007199254740993"}; b"\x00\xff" -> {"t":"bytes","v":"AP8="}; date(2026,10,2) -> {"t":"date","v":"2026-10-02"}; datetime(2026,10,2,9,30,+05:30) -> {"t":"datetime","v":"2026-10-02T09:30:00+05:30","fold":0,"tz":{"kind":"offset","seconds":19800,"name":"IST"}}; `"=1+1" -> {"t":"str","v":"=1+1"}`.

Decimal spelling, arbitrary-size integers, float hexadecimal values, bytes, dates, timezone offsets (and `ZoneInfo` keys), `None`, empty strings, Unicode, and formula-like strings have explicit representations. Unsupported values/timezone implementations fail with a stable safe error code; exception messages do not include result-cell values.

## Bounds and publication

Defaults are 1,000,000 rows, 100,000 pages, 16,384 columns, 50 MiB per selected snapshot or source page, 16 MiB per cell, 512 MiB output, and 1,048,576 XLSX metadata rows. The metadata-sheet limit is `min(configured_limit, 1,048,576)`, counting both contract/header rows; XLSX data rows also respect the physical worksheet limit. XLSX applies a conservative pre-compression bound to its write-only XML spool. No value is silently truncated.

The writer creates a temporary file in the destination directory, streams CSV/JSON or write-only XLSX, flushes and fsyncs it, then publishes it. Without `overwrite=True`, same-directory `os.link` is the atomic no-replace operation, including the final race window; filesystems that cannot do this fail with `ATOMIC_CREATE_UNAVAILABLE`. Explicit overwrite uses `os.replace`. Cancellation, incomplete chains, budget failures, and serialization errors remove only this operation's temporary file and do not publish a partial result. Directory fsync is best-effort on platforms that support it; the file itself is fsynced before publication.

## Verification and limitations

`python -m unittest tests.test_data_center_result_export -v` passed 13 tests (exit 0). The tests read back actual CSV, JSON, and XLSX files; check duplicate columns and typed values; verify formula-safe CSV headers/cells and XLSX cells, plus long text metadata; reject incomplete, truncated, mismatched, trailing, and column-metadata-drift page chains; exercise configured-versus-physical XLSX row limits, cancellation, and format-generation output limits; and race a competing destination into the no-overwrite publication window while checking that its bytes remain unchanged. The two new regression tests failed against the frozen implementation before fixes. Explicit overwrite, destination-temp cleanup, and openpyxl write-only spool cleanup on cancellation/failure are covered. All generated artifacts use `TemporaryDirectory`.

This package does not connect to a database, capture Qt model selection, fetch owner pages, or wire an export dialog. Full-query export only proves the iterable's page contract, not that a future provider truthfully reported its terminal page. The API is a typed writer seam for later UI/owner integration, not a claim that users can export from the product yet.

## Baseline and source hashes

The worktree was `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`. Reference copies for the unchanged input files are in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-20261002-7c83d234`. The prior frozen package byte baseline is in `C:\Users\Lenovo\AppData\Local\Temp\pengtools-result-export-fix-20261002-9e4b6790`: source `E153956D7F0BF50D5A332D93BED6E1A62821F99CB744CBFDF0716138B40E9AFC`; tests `031AD0D81835246CACEAB86D4693E7D86196AF18ABB5698EB62BC1FDBC109992`; report `D37BB85DBEFD2C63B0F22EDA02E47EC4C9407A8D4E1BA8BDE87332C143C86291`. Unchanged input hashes: `manual_session.py` `7C521011030DC135255B96F92FBA397E63ABEAE7CC96AF1F920D5653510A1DF1`; `result_model.py` `CBFAE113A5861EA7E49D8E265CC707DB3B7DC345753C89F357F871A59D0B9C30`; `DATA_CENTER_AGENT_REQUIREMENTS.md` `175558040DFEACAE21D9E5858D9D7D555EBE279DBA8C609A607B9D37B966BD45`; `requirements.txt` `1CBBA5AF5717A094F3FEE0357464FE1E971848FED194E767C27F780CD6682E38`.

Final source/test SHA-256 after the targeted fixes: `result_export.py` `B2E6E4A3721A093134D4BB3E9E9EE5AAC568B95834EDC1754C3A6129C59541FD`; `test_data_center_result_export.py` `AC07A5F8FE4CB1CE039E78C7E8E77FCB3919718056A4C4D2478CFE1AED6E5EC2`.
