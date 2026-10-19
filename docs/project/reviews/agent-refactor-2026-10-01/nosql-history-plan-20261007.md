# Redis / Mongo 草稿与历史接线计划（2026-10-07）

## 源码与需求边界

审查的是当前工作树字节，不把 HEAD 当作源码基线。当前 branch `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`；`panels/db_redis_panel.py` 与 `panels/db_mongodb_panel.py` 都有未提交修改。这里未读生产 profile、模型配置或凭据，也未改产品、Main、service、GUI、CONTROL、ACCEPTANCE 或 handoff。history application service 正在另一个作者边界内编写；本计划只写其所需公开契约，不推测那个实时 service 的实现或文件名。

需求 [DATA_CENTER_AGENT_REQUIREMENTS.md §7.3–7.4](../../DATA_CENTER_AGENT_REQUIREMENTS.md) 要求保留 Redis/Mongo 专用浏览器；Redis 增量 SCAN、集群部分失败、Key 类型/TTL/二进制和命令入口；Mongo 集合与 find/filter/sort/projection、文档/表格切换。Redis/Mongo 本地写操作与 Agent 分离，先预览/确认，按项显示结果，不提供 SQL 式统一回滚；取消不能承诺撤销已生效操作，Mongo 空条件删除首版拒绝。工作区 SQLite 保存草稿、历史、收藏与布局；恢复只恢复文本/上下文，不自动连接、重跑或恢复事务。

## 当前真实 route 与差异

| 场景 | 目前调用链 | 历史接线要保留的事实 |
|---|---|---|
| Redis 命令 | [`db_redis_panel.py`](../../../../panels/db_redis_panel.py) `_run_command()`：读 `cmd_input.text().strip()` → Redis `reject_reason` → `_current_conn()` → 追加命令回显 → `_run_worker('command', item, sql=...)` → `cmd_input.clear()`。`_RedisWorker.run()` 打开该 item、调用 `_run_redis(conn, sql, 0, 100)`、发 `completed`/`failed`，finally 关闭连接。 | 必须在 clear 前捕获原始 editor 文本、实际提交的 trim 文本、目标与执行类型。`_run_worker()` 在 shutdown 或单 worker 忙时会直接 return；调用方仍清空输入，所以未来接线要明确“已接受/未接受”，未接受时保留草稿且不得记作已派发 run。Redis 的 scan/bootstrap/overview 另走 `_run_bg()`，不是用户命令历史。 |
| Mongo 结构化查询 | [`db_mongodb_panel.py`](../../../../panels/db_mongodb_panel.py) `_run_query()`：从 `query_input` 读文本；未选集合时可由 `parse_mongo_query()` 提取并写入 `_selected_coll`；再次 parse 得到 `filter/sort/projection/limit`；派发 `_run_worker('query', item, collection=..., filter=..., sort=..., projection=..., skip=0, limit=...)`。查询编辑框目前不清空。 | 在 dispatch 同时捕获完整原文本和同一解析所得的 collection/filter/projection/sort/limit；不能只存 filter，也不能之后从可能已改变的输入框重建。结果文档不写进 history。分页 `_shift_page()` 当前只重解析 filter，使用新的 skip 和固定 limit 50，没有把 sort/projection/原 limit 传入 worker；历史应分别保留原草稿上下文与实际派发参数，不能伪称它们相同。 |
| Mongo Shell 命令 | `_run_command()`：读取/校验 `cmd_input` → 捕获当前连接 → `_run_worker('command', item, sql=...)` → clear。worker 内才 parse 命令，并用解析出的 collection/filter/sort/projection/limit 调 `find_docs()`。 | 和 Redis 一样必须先捕获再 clear，且 dispatch snapshot 需包含完整 shell 文本及解析出的结构化上下文。使用同一纯 parser 结果绑定历史与执行，避免历史内容与实际 worker 参数分叉。 |
| 完成与清理 | 两种 worker 都是 QThread，暴露 `completed`/`failed`，退出时发 Qt `finished`。面板在 `workbench_shutdown_cleanup()` 中取消并断开 UI 的结果槽，但保留 worker 引用直到 `finished` 清理槽；面板结果槽也会忽略 shutdown、已替换的 worker 或已取消任务。 | 应用 history service 必须在 `worker.start()` 前绑定本次 immutable run id、completion/failure 和 `finished`，成为唯一 completion/finished sink。`finished` 时恰好 finalize 一次；若取消/关闭导致没有 terminal signal，仅在实际 finished 后记 UNKNOWN；取消请求本身不是 CANCELLED 结果证明。UI cleanup 不可断开 service 的 observer。不要让 panel 自己另写 SQLite 或存第二份状态。 |

当前两个 panel **没有查询草稿 tabs**。Redis 的 `side_tabs` 是 Overview/Key details/AI/Agent，`value_tabs` 是 String/Hash/List/Set/ZSet 数据展示；命令 console 只有单一 `cmd_input` 和共享输出。Mongo 的 `side_tabs` 是 Documents/AI/Shell/Agent，文档/表格是同一结果视图切换；`query_input` 与 shell `cmd_input` 仍各只有一个。这些 module/view tabs 都不是独立查询上下文。Redis `_put_data_center_agent_command()` 只把文字塞进 `cmd_input` 并 focus；调用它、覆盖 QLineEdit/QPlainTextEdit 或新建隐藏 helper，都不等价于新建一个可见、可持久化、有独立身份的 draft tab。

## 等 history service 冻结后需要的公开契约

下面只是双方待签的 API contract 名称，不表示当前已有这些接口：

```python
history_service.bind_nosql_worker(
    capture: NoSqlRunCapture,
    worker: QThread,
) -> NoSqlRunId

history_service.load_nosql_draft(
    history_id: str,
) -> NoSqlDraftSnapshot

panel.create_history_draft_tab(
    snapshot: NoSqlDraftSnapshot,
) -> DraftTabId
```

`bind_nosql_worker()` 在 QThread 启动前登记 immutable dispatch、连接成功/失败事件与 `finished` sink；shared history store 只由 application service 管。`NoSqlRunCapture` 至少包含 engine、动作类型、未裁剪 editor 文本与有效派发文本、opaque `connection_id + profile_revision`、Redis mode/db index 或 Mongo database/collection、用户 query 的结构化参数，以及本次实际 `skip/limit`。run id 由 service 在绑定时生成并返回。Mongo 参数须保留原文本和可恢复的 filter/projection/sort/limit/collection；使用有界、版本化、BSON-aware 编码，不 pickle/eval，不把不可表达的 BSON 值静默变成不同查询。若不能安全解码，草稿仍可读但 Run 禁用。

快照采用 allowlist 保存目标引用，不复制 `_current_conn()` 的整行，不持久化 URI/seed/auth/password token、连接配置、结果文档、Redis 值或大输出。只保存文本/上下文和有限 terminal metadata（有证据的状态、时间、精确结果数量或None、有界结构化证据）；失败只保留安全状态代码，不保存raw exception或错误正文。数据库结果仍按现有面板逻辑展示。

`load_nosql_draft()` 必须恢复捕获的目标引用，而不是当前 combo 值。应用 host 通过只读目标解析 contract 验证原 connection id/revision 仍匹配；Redis 还核对原 mode/db index，Mongo 核对原 database/collection 和 query 内 collection。缺失、revision drift、目标冲突或必需上下文不全时，仍创建可见且可复制的文本草稿页，但 Run disabled 并显示具体原因。**绝不回退 `_current_conn()`，不自动切换现有连接，不自动刷新 Key/collections/schema，不 connect、不运行、不写入、不恢复结果/事务。**准确目标可用时只解除 Run disabled；数据库连接仍仅由用户明确 Run 触发，并经原面板 dispatch、权限/危险操作提示和 worker 路径。

每次 restore 都要有新的 `DraftTabId` 和新的实际可见 QWidget/tab 实例；不覆盖当前输入框、不把历史直接送 `_run_command()`，也不因“创建成功”自动触发连接/metadata。运行中的目标固定为这个 draft 所 pin 的 id/revision/db/collection；切换 combo 不可改写已运行任务的 target。NoSQL insert/delete/Redis 写操作继续沿用现有确认与危险操作守卫，不加统一 rollback，也不改变 cancellation 的不保证语义。

## 有限实施边界建议（本轮不实施）

- 修改 `panels/db_redis_panel.py`：在单一命令输入之上增加真正的 draft-tab state/widget；dispatch 前组装 allowlist capture，在 worker 被接受后交给 service 绑定；未接受则保留文本。历史恢复创建新页，页内固定 Redis id/revision/mode/db index。保持 scan/key/value/TTL 与写操作流程。
- 修改 `panels/db_mongodb_panel.py`：Documents query 和 Shell command 两种编辑入口各保留独立 raw text 与实际解析结构；新增可见的独立 draft tab 实例，固定 id/revision/database/collection；history restore 仅调用 panel 公共 `create_history_draft_tab()`，不走 `_on_connection_changed()` 的自动 metadata 路径。不改当前 Mongo 写入预览/确认、空条件删除拒绝和 worker cleanup 契约。
- history service/store 的 schema、迁移、查找/收藏 API 由其作者维护；在其公开 API 冻结前，这两个面板不绑定私有 helper，也不自行读取/写入 SQLite。若服务不能在 worker start 前挂接 completion/finished，先由 owner 给出稳定 bridge contract，再开始面板实现。
- 新增 `tests/test_nosql_history_routes.py` 与 `tests/test_nosql_history_draft_tabs.py`；避免修改现有 `tests/test_data_center_manual_integration.py` 或 history 作者冻结文件。生产代码与测试最终 owned list 需在 service API 冻结后再登记；当前只建议这两个 panel 和这两个专用 test 文件，不包含 Main、application history service、CONTROL/ACCEPTANCE/handoff。

## 定向 fake/QThread 与视觉证据

1. 使用真实 QThread worker，但将 `open_connection`、Redis client、Mongo client、profile/revision resolver 和 history store 全部换成 fake；不导入生产 profile，不连本机/真实库。给 combo 提供两个不同的 synthetic connection id/revision/db/collection，断言 worker 实际拿到恢复草稿原 target，而不是运行时当前选中的第二个 target。
2. Redis：在 `_run_command()` 调用时先记录带前后空格的原 editor 文本及 trim 后执行文本；验证 history capture 发生在 clear 前，worker busy/shutdown 未接收时不丢草稿且没有 run record。正常/异常/取消/面板 shutdown 各验证 service 收到一次 terminal；面板断开 UI 槽不影响 service 的 `finished` 收尾。
3. Mongo：分别覆盖 `_run_query()` 与 Shell `_run_command()`；断言 raw text、database、collection、filter、projection、sort、limit 全部原样恢复，Typed BSON sentinel 往返，输入 clear 前 capture；验证分页事件保存真实 worker kwargs 与基础 draft 分开。目标不存在或 revision drift 时，新草稿可见可复制、Run disabled，且 `open_connection`、`list_collection_names`、schema sampler 和查询 fake 调用数全为零。
4. Redis/Mongo 手动写风险流程加回归保护：history restore 不可直接调 worker，运行须经过现有验证/预览/确认入口；测试不对真实数据发命令，不声称有统一 rollback。
5. 完成后做实际窗口 capture：两种面板各在 960×640 下，用中文与英文、17px 与 21px 请求字号组合，共八张状态图；记录各正文/按钮/编辑器实际有效字号与DPR，不把parent请求字号当成子控件实际字号。至少包含两个 draft tab、长 Redis 命令、带 filter/projection/sort/limit 的 Mongo find、target pin/Run-disabled 状态。截图确认 tab 真正可见且切换后文本/context 独立，命令/文档区没有被挤没；本计划阶段没有运行 GUI 或产出视觉证据。

history service API、panel 内 tab model 与 compact layout 尚未冻结，且这两个面板源码是脏工作树版本。因此此文仅为可审核的接线方案；提交实现前须由 root 与 history 作者确认 capture DTO、worker terminal 状态机、tab host seam 和无生产数据测试边界。
