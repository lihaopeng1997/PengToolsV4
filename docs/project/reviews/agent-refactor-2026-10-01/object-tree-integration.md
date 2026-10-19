# Object tree integration checkpoint

## Scope and source identity

- Branch: `dev/2026-09-23`; HEAD: `818fba72bf437de9bd49fcd5b198fb1270096a53`.
- Initial integration baseline: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-integration-20261002-ld3r_bf4\baseline.json`.
- Tooltip/theme follow-up baseline: `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-integration-followup-20261002-1790950098\baseline.json`.
- This checkpoint wires the signed metadata tree into the existing Data Center panel. It does not implement table data browsing, editing, or a new transaction flow.
- No real configuration, database, model, or user UI was used. The fixtures use the production panel/composition/host classes with TEMP profiles and fake connector/client implementations.

## Behavior verified

- Creating, showing, selecting, filtering, rebuilding, and expanding the catalog tree performs no metadata connection until a user explicitly expands a connection. Filtering and catalog refresh preserve signed tree nodes and do not trigger another read.
- Each opened directory has its own composition and controller. Results are accepted only for the current signed target, node reference, generation, request, and cursor. A refresh capture blocks old-target pages from replacing the last-good view.
- Redis requires an explicit prefix or an explicit “all keys” choice. Only signed Redis key nodes are inspected; other object kinds show the available directory behavior without returning fabricated table data.
- Pages are capped at 50 rows, with a cumulative 2,048-node limit per connection subtree. Rejected growth keeps the prior page. Closing one directory waits for that directory's cleanup and leaves other directories usable; global panel cleanup includes directories that were never opened as native workspaces.
- Every object row's tooltip carries its complete signed `node.name`, retaining case and whitespace, plus any localized notice; Redis detail tooltips keep the full key name. The actual-panel fixture verifies the tooltip and confirms its `node_ref` remains unchanged across catalog rebuild.
- A TEMP production-panel check opened workspaces A and B and placed A in the current tab with a sentinel manual-session snapshot. Selecting B in the tree, refreshing/rebuilding the catalog, and closing B's directory owner left A as the current workspace throughout; the snapshot and sentinel identity stayed unchanged, and A's manual/workbench/agent cleanup methods were not called. This checks panel routing and retained context, not a live database session.

## Verification

- `\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_object_tree_integration -v` — 8 tests passed.
- `\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_workspace -v` — 17 tests passed.
- `\.venv-build\Scripts\python.exe -m py_compile ui/data_center/workspace_tree.py ui/data_center/object_tree_workspace.py panels/data_center_panel.py tests/test_data_center_object_tree_integration.py` — passed.
- `git diff --check` — exit 0; Git printed existing LF-to-CRLF notices for unrelated dirty files, with no whitespace errors.

## Visual evidence

The screenshots show the actual `DataCenterPanel`, production `ObjectTreeComposition`, and production host classes using TEMP fake connectors. The capture fixture applies the same style API used during app startup: `ThemeManager.instance().apply(app, "calm", font_size=13)`. Here `font_size=13` is a stylesheet pixel size, not 13pt. Captured with `QT_QPA_PLATFORM=windows` and device-pixel ratio 1.5; resolved family was `Microsoft YaHei UI`. Actual font metrics: QApplication font 9pt/12px; panel, tree, and dialog 9.75pt/13px; button 9pt/12px. The fixture selects the TEMP connection and verifies five actions are visible, enabled, within the panel, and at least their minimum size hint before rendering. It does not launch the user application or connect to a real database.

| Locale and size | TEMP screenshot | SHA-256 |
|---|---|---|
| zh, 960×640 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-integration-followup-20261002-1790950098\screenshots\data-center-object-tree-zh-960x640.png` | `A4CA940BB789882E95C2B82E5F6CE6E81A0D0D84732B868FFC38FC3BF0D17E19` |
| en, 960×640 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-integration-followup-20261002-1790950098\screenshots\data-center-object-tree-en-960x640.png` | `930AE597260847A8056F5B0FD3828C32CA0CA3DCFDEB53F6233C96DEB325D4A3` |
| zh, 1160×720 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-integration-followup-20261002-1790950098\screenshots\data-center-object-tree-zh-1160x720.png` | `2E123D87113E08148E0742DA3BD6F75AE43ABDD493B2B4D4B8ADFB5FAFF0592B` |
| en, 1160×720 | `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-integration-followup-20261002-1790950098\screenshots\data-center-object-tree-en-1160x720.png` | `4900BD5A0A4DB97C88EE3DF9DEC9E8E779B862B53E64173FEEC18183F3DAFEAE` |

## Frozen file hashes

| File | SHA-256 |
|---|---|
| `ui/data_center/workspace_tree.py` | `F53CF1267CB47C09B9E1C89108A8E27DC1D900A2A0863CDC7FE5A8A585207349` |
| `ui/data_center/object_tree_workspace.py` | `6A628EDCB5925C04FC6F215459800810386F2DEC4B0A309AA2D0F58ABB28B8C4` |
| `panels/data_center_panel.py` | `11DCAAA75145F542F7009F8A18FD236BDFE2D2E2A04A262680848A24B148878F` |
| `tests/test_data_center_object_tree_integration.py` | `931BC25985D966A560DB854B5CB0D499CFFDFCDB41FF4C50090A451EC5970554` |
| `ui/data_center/object_tree_controller.py` (unchanged) | `DFC5B216C3898B6E96C009AD122712BC94E8EBB1D0B6FBB219E798F6D974AC74` |
| `tools/data_center/object_tree_composition.py` (unchanged) | `9B16D84FBEA224081E3AD03DAE5D944C96F3DB330009A8B5A9FD713CE6A493F9` |
| `tools/data_center/object_tree.py` (unchanged) | `6D08FD2A494A59DA031E658131A7D114115252D3DF718474109432563DD59631` |
| `main_window.py` (unchanged) | `52B43C2D03D728733E9B8E1FB02A28A5F054BB8D03A29C99EB2F71C2E10D07B0` |

Real database behavior, live manual-session behavior, and whole-application exit remain outside this checkpoint. No staging, commit, or push was performed.
