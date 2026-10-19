# Data Center object tree backend

This package adds a lazy, user-only metadata tree. `ObjectTreeService.root(TreeTarget)` signs a zero-I/O root node. Child expansion and Redis key details return `ObjectTreeRequest` handles (`request_id`, `future`, `cancel()`); results are immutable `TreePage` or `TreeNodeDetails` DTOs. Only service-issued opaque `node_ref` values can select a path. Database/schema/key names are display or node-registry data and are never accepted as independent scope authority.

The service uses closed catalog templates for MySQL, Oracle, OceanBase MySQL mode, and OceanBase Oracle mode. Redis and Mongo use separate metadata-only factory/lease entry points. Redis raw byte identities stay in the in-memory node registry; `TreeNode.name` is display-only. Keys larger than the 4 KiB identity budget produce `redis_key_identity_over_budget` and do not prevent later keys in the page from appearing. Redis TYPE/TTL requires a valid signed key node. The Agent's normal SCAN remains on its prior 256-byte preview path, with no binary identity field or new model-visible tool.

Refresh drops descendant refs and cursors, and each completion is checked against both request generation and refresh epoch before it can issue nodes or enter the cache. Targets, parent paths, and cursors are bound together. Queue size, worker count, node/cursor/cache entry counts and bytes, active metadata hosts, and TTLs have fixed limits. `close()` cancels outstanding requests cooperatively and resolves its `Future[CleanupResult]` only after the ObjectTree worker and metadata-host workers have actually exited; it does not terminate a blocked driver call.

`MetadataHost.directory_children()` and its fixed template IDs are a separate user-tree path. `MetadataHost.execute()` remains limited to `search_objects` and `describe_object`, and the Agent SQL policy was not changed. The object-tree business module imports no UI, driver, config, or connector module.

The existing read-only connector/template path does not provide a Dameng directory template. Dameng targets therefore return the explicit `dameng_directory_unavailable_readonly_connector_not_wired` notice without building a database host or calling a driver. This leaves Dameng object-tree support pending a reviewed read-only catalog adapter. All tests here use fake providers; they do not prove behavior against a live vendor driver.

The service contract is:

```python
TreeTarget(connection_id, profile_revision, provider, mode="", database="", redis_prefix="")
service = ObjectTreeService(relational_host=..., nosql_host=...)
root = service.root(target)  # no DB I/O
request = service.submit_children(target, root, cursor=None, refresh=False, generation=0)
page = request.future.result()
cleanup = service.close()  # Future completes after real worker shutdown
```

`relational_host` must be the existing host that provides `profile_snapshot()` and lazy `create_factory()`. `nosql_host` must provide `profile_snapshot()` and the separate `create_directory_factory()`. UI code should receive these hosts from application composition; it must not construct connectors or inspect private panel fields. That production application-composition/UI wiring is not part of this backend package.

## Verification

All verification used synthetic profiles and fake database clients. The focused ObjectTree suite covers all implemented provider modes, paging and cursor invalidation, duplicate display names with distinct Redis byte identities, over-budget key notice plus continued rows, forged/cross-target/expired refs, refresh/generation late-result gating, registry/cache limits, and relational and NoSQL worker shutdown. Existing boundary suites cover closed metadata template IDs, the unchanged Agent metadata tool surface, NoSQL directory-vs-Agent separation, and unchanged Agent SCAN preview behavior.

## Exact verification

The initial full package focus ran 70 tests successfully before the 2026-10-02 lease-cleanup increment:

```powershell
python -m unittest tests.test_data_center_object_tree tests.test_data_center_metadata_runtime tests.test_data_center_metadata_driver_boundary tests.test_data_center_nosql_clients tests.test_data_center_host_nosql -v
```

Python compilation also succeeded:

```powershell
python -m py_compile tools/data_center/object_tree.py tools/data_center/host_metadata.py tools/data_center/readonly_driver_adapters.py tools/data_center/host_nosql.py tools/data_center/readonly_nosql_clients.py tests/test_data_center_object_tree.py tests/test_data_center_metadata_runtime.py tests/test_data_center_metadata_driver_boundary.py tests/test_data_center_nosql_clients.py tests/test_data_center_host_nosql.py
```

`git diff --check` reported no whitespace errors in the diff. It printed existing line-ending conversion warnings for unrelated dirty files. A trailing-whitespace scan over the files listed below found none. The checkout remained on `dev/2026-09-23` at `818fba72bf437de9bd49fcd5b198fb1270096a53`.

Final SHA-256 values for the object-tree package source and tests:

| File | SHA-256 |
| --- | --- |
| `tools/data_center/object_tree.py` | `6D08FD2A494A59DA031E658131A7D114115252D3DF718474109432563DD59631` |
| `tools/data_center/host_metadata.py` | `9D23813AD3D1492987D71DC23AB3A5D812BDC6B690E0685A03A6A86597B6CAE1` |
| `tools/data_center/readonly_driver_adapters.py` | `EE7ACFDE528761D2B5EEE06045678B018389E354648106892D8D4C37B53BC5B1` |
| `tools/data_center/host_nosql.py` | `0E3F9ABC80A481C0B520C44D97607193EF47C826AFE297CAC7414C6500E2CC03` |
| `tools/data_center/readonly_nosql_clients.py` | `FBE590BAF3EA048AB73D456D73E9579526FE6D31C19EC1382C46EF724BDC7D3C` |
| `tests/test_data_center_object_tree.py` | `6A6011CDC8C72E93FA4DE0F97AD4A17F9D4F5BFD3FCD6EBF3771AD55FB915A19` |
| `tests/test_data_center_metadata_runtime.py` | `B123D27B7C8AA346D7D6495903077D455B7438CAEC2B9A0936ABB2E122B34199` |
| `tests/test_data_center_metadata_driver_boundary.py` | `B533F73D2E06C9B02A694A64DAE4D8B922878356ED1E6182DEC5EAC36C2566AE` |
| `tests/test_data_center_nosql_clients.py` | `4A739F20721DAB401AFF2C06AD3D87829768C23E50E6139EDAE18C6EDA2D10FB` |
| `tests/test_data_center_host_nosql.py` | `873F413E5BE404812E30B6A6EB591B823E1DC5C239EFC700E201C272FA92E391` |

The TEMP pre-edit snapshot at `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-20261002-3a6fd9c78f7d4b9aa1c34dc7a5de9863` recorded these starting hashes for existing seams:

| File | Pre-edit SHA-256 |
| --- | --- |
| `tools/data_center/host_metadata.py` | `382E867C0B318DA9AF3B9C9517A39B7976E20DCEAB7594295876D04494B07BA6` |
| `tools/data_center/readonly_driver_adapters.py` | `6C152EEC0C91BBC07AF2F04D149F7D0E5B3FD3379AC15A9D1DFE0577F55B674D` |
| `tests/test_data_center_metadata_runtime.py` | `C8F3AD75D506CB24AC75FFA6F347F8B5014D1FC065CC82DF919CD0459830718C` |
| `tests/test_data_center_metadata_driver_boundary.py` | `296A32A3CCD8CD358F6115DAC369FE6CE726AEFDE4137096B96960AC9002A187` |
| `tools/data_center/readonly_nosql_clients.py` | `4E4627CA0EBC557777F7789945EDF554D181824AEF6CF07D3C81686A7600C545` |
| `tools/data_center/host_nosql.py` | `B2E43245EA80A8F2BA4B8756A65BB799CB80B03E696ADA8B9AF3982171D6E2DF` |
| `tests/test_data_center_nosql_clients.py` | `A7FD370981142C74D99DDC56C6228EBF71D7DE1FB4A590A6BD9AEF2073EA703F` |
| `tests/test_data_center_host_nosql.py` | `C552BDB194B4EBAE619DF38404569D2827EF53F99637163D2CB3B42BF0577EC6` |

## 2026-10-02 NoSQL lease cleanup increment

After a NoSQL factory returns a lease, profile revision/snapshot verification and the directory-only marker check now share one cleanup boundary. If either check fails, the newly opened but not-yet-registered lease is closed immediately on the ObjectTree owner worker. Safe `PROFILE_MISMATCH` and `PROFILE_UNAVAILABLE` codes are preserved, and no directory call is made. The lease is only added to the service registry after both checks pass; `close()` remains idempotent and waits for the owner worker/factory cleanup.

The incremental regression command ran 13 ObjectTree tests successfully:

```powershell
python -m unittest tests.test_data_center_object_tree -v
```

This delta also compiled successfully with:

```powershell
python -m py_compile tools/data_center/object_tree.py tests/test_data_center_object_tree.py
```

The mismatch, unavailable-snapshot, and invalid-directory-marker cases assert no directory request, one lease close on the same owner thread that opened it, safe error codes, and repeatable service cleanup. The pre-delta snapshot is `C:\Users\Lenovo\AppData\Local\Temp\pengtools-object-tree-leasefix-20261002-01f4731e6c6d4eef9f125dc23a4e6763`: `object_tree.py` `63C6525D2A77AEA82A5AA9C5581DE20DAFCA482E246E0D995AD03C274A48285D`, `test_data_center_object_tree.py` `77FD8278A1B1659F47A8649523B370F03B7BF25B21CA25D61A29A784600B9C07`, and this report `B27843F7823DE2BA20219E1A50EB95EFFC86A64807A364D9F6A97376CBFD79CE`.

No real connection, user data, configuration, credential, or model/network call was used. The production host composition and UI lifecycle integration remain separate work.
