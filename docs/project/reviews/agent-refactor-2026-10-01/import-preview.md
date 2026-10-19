# CSV/XLSX import preview foundation

Date: 2026-10-04

## Scope and baseline

This is a pure file/mapping foundation for the DC-05 relation-table import requirement in DATA_CENTER_AGENT_REQUIREMENTS.md §7.4. It does not implement database insertion, a dedicated database session/transaction, import progress UI, commit/rollback controls, or real-database acceptance.

Baseline branch and commit: dev/2026-09-23, 818fba72bf437de9bd49fcd5b198fb1270096a53. The working tree already contained substantial unrelated changes; they were left untouched. These three owned paths were absent before implementation:

- tools/data_center/import_preview.py
- tests/test_data_center_import_preview.py
- docs/project/reviews/agent-refactor-2026-10-01/import-preview.md

No dependency file or lock was changed. Runtime evidence used Python 3.12.3 and the preinstalled openpyxl 3.1.5. Baseline SHA-256 values:

| Item | SHA-256 |
|---|---|
| requirements.txt | 1CBBA5AF5717A094F3FEE0357464FE1E971848FED194E767C27F780CD6682E38 |
| openpyxl/__init__.py | B36B17729F138575C7B3035287E52E422E411ECA1AB2E7335328CD373D15BA97 |
| openpyxl/reader/excel.py | 9204AD42D3B58F4BD5E7A196697C68DC6036117BA8B86B6716B45532872AF046 |

## Contract

CsvSource requires a path, encoding, and delimiter. XlsxSource requires a path and exact worksheet name. Constructing sources and plans performs no file access. inspect_import_source() is the explicit file action: it copies and hashes the complete bounded source into an owned, read-only TEMP snapshot, parses that exact snapshot, verifies the original path still has the approved fingerprint, returns physical source-column indices (so duplicate labels stay distinct), and reads only a finite sample. This prevents a same-size, same-mtime transient edit to the user path from changing the bytes parsed after inspection.

preview_import() applies caller-supplied TargetColumn, ColumnMapping, and required NullRule values. Mapping uses source indices, requires unique target names, and accepts no guessed database schema, primary key, default, or SQL. CSV uses exact NULL tokens and a separate empty-string rule. XLSX uses exact NULL tokens, an explicit empty-text rule, and a separate blank-cell rule. Dates in text cells require a caller-specified format; native XLSX date values remain typed. Decimal text and XLSX numeric XML lexicals become Decimal; target precision/scale is checked without rounding. Errors contain only a safe code and optional zero-based row/column indices.

The initial type set is text, integer, Decimal, date, and datetime. Boolean and other unsupported XLSX cell types fail with a safe conversion error instead of being coerced.

The default limits are 32 MiB source bytes; 2,048 ZIP entries, 32 MiB per expanded XLSX entry, 128 MiB total expanded XLSX bytes, and compression ratio 200; 512 columns, 250,000 physical rows, 2,000,000 cells, 100 preview rows, 1,000 rows per chunk, and 131,072 characters per cell. Caller limits can lower these values and are clamped by hard ceilings. XLSX entry, expansion, relationship, dimension, and row/cell metadata checks run before openpyxl. Workbooks are opened with read_only=True, data_only=False, and keep_links=False; formula cells are rejected and never evaluated.

approve_import(preview, user_approved=True) is a separate explicit action and grants no database-write permission. iter_import_chunks() is one-use per approved snapshot: it verifies the original source and snapshot before parsing, parses only the snapshot, checks original path identity/metadata before chunk yields, and rehashes both snapshot and original source at successful exhaustion. Ordinary or transient changes to the original path cannot alter parser values; detected source changes still fail the operation. A change detected at the terminal check may be reported after earlier chunks were yielded. The owned snapshot is deleted after iterator exhaustion, cancellation, error, or generator close. close_import_snapshot() explicitly releases an abandoned preview; when a reader is active, deletion waits until its file handles close. Callers must close previews they abandon instead of treating UI reference disposal as completed import cleanup. Future transaction integration must write only inside a dedicated import transaction, exhaust the iterator before offering commit, roll back only that import transaction on any error/cancel/source change, and commit only after the user's separate decision.

## Verification

Command: python -m unittest tests.test_data_center_import_preview -v

Result: 25 tests passed. Temporary CSV/XLSX files exercise UTF-8 BOM and explicit GB18030 decoding; duplicate headers mapped by physical index; exact NULL, empty-string and blank-cell rules; exact high-precision decimal XML lexicals; explicit date parsing and native dates; malformed row widths; finite preview and lazy chunks; formula rejection in preview and later rows; XLSX ZIP/metadata limits before openpyxl; cancellation; explicit approval; fingerprint changes before iteration, after a full-size final chunk, and after a partial final chunk; same-size, same-mtime transient original-file edits that are restored around the real CSV and XLSX readers; one-use snapshots; and cleanup on cancellation, explicit abandonment, and generator close. The final partial chunk is only provisional until the consumer resumes the iterator and terminal cancellation/source checks pass.

python -m py_compile tools/data_center/import_preview.py tests/test_data_center_import_preview.py passed. A fresh module import reported openpyxl_loaded_on_module_import=False. No live database, transaction, UI, or visual acceptance was attempted; those remain pending under the root DC-00–DC-06 goal.

## 2026-10-06 XLSX row-width correction

The Oct 6 resume reproduced `MALFORMED_ROW` through the public `XlsxSource`
inspection path for rows with omitted trailing cells and for an explicit
all-blank worksheet row. A separate row containing a cell past the header
width also returned `MALFORMED_ROW` and remains rejected. The pre-edit source,
test, and report copies and SHA-256 manifest are in
`C:/Users/Lenovo/AppData/Local/Temp/pengtools-import-xlsx-tail-before-20261006/`;
the saved source hash matched the expected accepted baseline
`A295415BB9E41A8460B7767C6BE607D00CE1A72224648F91205693440260152F`.

The parser now materializes omitted XLSX cells as explicit `BLANK` raw cells
only through the verified header width. Sparse rows and all-blank data rows
therefore keep physical column indices and flow through the existing blank vs
empty-text rules. Extra data cells remain malformed. Padded cells and missing
worksheet rows count against the configured cell budget. CSV width checks,
formula rejection, snapshot hashing, original-path checks, cancellation, and
iterator terminal verification are unchanged.

The regression follows inspection, typed preview, explicit approval, and
complete approved-chunk iteration for a three-column sheet containing a
missing trailing nullable cell and an all-blank row. It checks explicit blank
cell kinds, typed values, and snapshot deletion after successful exhaustion;
an extra-column regression still fails closed. A dedicated budget regression
confirms the synthetic cells needed to reach the verified header width count
against `max_cells`; the CSV missing/extra-width regression remains malformed
as before. The baseline reproduction and repaired path both go through the
public `XlsxSource` → inspection → preview → approval → chunk-iterator flow,
not an internal parser-only shortcut.

The parser-only command
`.venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_import_preview -v`
passed 26 tests. The final combined importer command, run with
`QT_QPA_PLATFORM=offscreen`, was
`.venv-build/Scripts/python.exe -B -m unittest tests.test_data_center_import_ui tests.test_data_center_import_session tests.test_data_center_import_preview -v`;
all 55 tests passed. The same command also passed under
`QT_QPA_PLATFORM=windows`; a separate synthetic Windows-QPA matrix with an
explicit `QApplication.platformName() == "windows"` assertion captured and
checked the dialog across the requested 960/1160 logical widths and 13/16pt
Chinese/English fonts (details in `import-ui.md`). `py_compile` passed for
the changed Python source and test files, and `git diff --check` reported no
whitespace errors in the owned paths. This correction uses only temporary
CSV/XLSX fixtures and does not access live database, configuration,
credentials, production profile, or user application data.

The separate importer UI delta is recorded in `import-ui.md`; existing
ManualQuery entry wiring and host shutdown aggregation remain pending.
