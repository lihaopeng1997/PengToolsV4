# NoSQL 查询历史产品接线验收记录

本记录覆盖 Redis/MongoDB 查询历史的六路径接线。它验证 TEMP 假数据库与隔离工作台，不代表连接过真实数据库、读取了真实历史载荷或调用了模型。

## 实现边界

Redis 与 MongoDB 面板在 Run 边界冻结固定连接身份和准确编辑文本；Mongo Documents/Shell 只解析一次，worker 与历史记录复用同一组 filter、projection、sort、skip、limit、collection 和 BSON 类型值。分页仅推进 skip，并保留首次 Run 的实际 limit 语义。Documents 的行数只接收同 filter 的严格非负 `int` total；Redis 和 Shell 不从普通结果猜行数。

应用级历史 sink 独立于面板信号连接，并持有 worker 到真实 `finished`。匹配终态只记录一次；finished 前没有终态时写 `UNKNOWN`。历史写入失败只报告安全状态，不会令已经派发的数据库动作留下待重复的 Run 输入；busy、shutdown、启动前拒绝会保留输入。历史恢复打开新的锁定 draft，保留原 connection id、revision、database、collection、mode 和参数。profile revision 通过注入的只读 profile host 重验；这个 loader 可以读取被注入的数据源，因此此行为不称为绝对零 I/O。恢复、切换 tab 不连接数据库、不刷新元数据、不执行事务，也不回退到当前连接选择。

Mongo Insert/Delete 仍经过现有确认流程；对固定 draft 目标的确认提示包含连接、数据库、集合。拒绝确认不会启动 worker 或连接数据库。产品没有新增数据库、模型或 profile 存储格式。

## 隔离与验证

最终整模块命令为：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
.\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_nosql_history_integration -v
```

结果是 8/8 通过、无 skip。该轮覆盖七个运行/生命周期用例和一个 Windows QPA 视觉矩阵用例。测试在构造前注入 TEMP profile/model loaders，面板自身和嵌入式 Data Center Agent 都断言持有同一 fake host；`config.DATA_CENTER_WORKSPACE_FILE` 指向 TEMP SQLite，应用历史 singleton 与既有实例隔离；layout load/save 在 widget 创建前改接 TEMP 记录器。数据库打开函数只接假 Redis/Mongo 驱动。teardown 等待所有 fake DB worker、Agent catalog/controller 与历史 controller 实际结束，调用 `DeferredDelete`，之后才恢复 patch、清理 TEMP 文件。被恢复历史的 profile revision 变化会调用且只调用注入的 TEMP profile loader。

`py_compile` 对五个 Python owned 文件通过。owned tracked 文件的 `git diff --check` 通过；新 Python 文件尾随空白检查通过。视觉结果是 8 张 Windows QPA `calm` 截图，逻辑工作区与 host 都为 960×640、低高度 narrow 模式，DPR 为 1.5，PNG 为 1440×960 物理像素。zh/en 两种语言都有 Redis 与 Mongo 画面；17px/21px 核心编辑器、Run、结果和操作区控件均记录为 Microsoft YaHei UI，关键控件 bounds 全部位于逻辑 960×640 内。Mongo Documents 在 17px 画面、Shell 在 21px 画面，两种语言都有覆盖。图像与坐标见 TEMP `evidence/screens/windows-calm/`，逐图元数据在 `screen_manifest.json`。

更早的一轮严格隔离复测曾有三个 teardown 断言失败；失败的是我加入的过严 fixture 断言，错误地假定每个普通 draft 创建都必须调用 profile loader。普通 draft 用传入的已捕获 profile 构造，不需要重读 loader；历史恢复才重验 loader。该诊断日志保留在 TEMP `evidence/final/isolated-suite-windows-qpa-profile-assertion-diagnostic-failed.log`。修正后新增构造身份断言和恢复 loader 调用断言，最终整套 8/8 通过。

早期 direct-panel 测试早于完整 loader 注入审计；不能证明那些尝试没有读取默认模型 catalog。它们可能读过已配置 catalog，但没有打印或复制 catalog 内容，也没有发送模型请求。最终通过的复测仅使用 TEMP loaders；这项披露不把早期尝试算入隔离验收。

Offscreen 下的独立顶层面板尺寸探针未通过 960×640：先前记录的观测是 995×822，原始日志未能恢复；本次在完整 TEMP 隔离下重现为 Redis 960×807，minimum size hint 为 756×807。记录在 TEMP `evidence/final/offscreen-probes.json`，不将其算作视觉通过。该布局保留隐藏的嵌入式 Agent tab 尺寸约束。授权的 Windows 视觉矩阵使用实际 `DataCenterPanel._default_host_factory` 和 `_ConnectionWorkspace` 嵌入调用及生产 narrow/low-height 初始化，实测达到 960×640，8 张图 bounds 均通过。

## 原始字节和哈希边界

初始 manifest 在 TEMP `baseline.json`，时间为 2026-10-07 16:20（本地时区），分支 `dev/2026-09-23`、HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。初始源哈希为：Redis `C859AC2293C513EA5B0C7DEE66B43328B2C6BB06F08B57AE9C2F15C21F426406`，Mongo `A22846F1711A5F9E3FD1C749AB6E17A32CF0150FF7F168649542E6AA07E14A22`，history service `856A5962A65C6FBB244D441B9FCC1395B6B892401D777D180ADA647E8B99620E`。三个新增路径初始均不存在。

原始 Redis/Mongo 字节未从现存归档中恢复。TEMP 中曾命名为 `before/` 的副本实际晚于首次编辑；现改名为 `late-checkpoints-never-baseline/`，明确不作为 before。Redis checkpoint 哈希为 `773E7C911EBCCBAC7E796CDDBD9C0D349794F1A77041F0E04EC43C39B60051AD`，Mongo 为 `B57C7E09763F0B6EABCA22B623026500783D5536D96F8CD2C06CFD683AFA1B61`。history service 的原始字节可从既有 root 接受包恢复，TEMP 副本与初始哈希 `856A5962A65C6FBB244D441B9FCC1395B6B892401D777D180ADA647E8B99620E` 完全匹配，来源为 `evidence/root-history-product-20261007/author-first-final/final_source/ui/data_center/query_history_service.py`。

最终六路径哈希、before 证据状态、八图哈希/bounds，以及 protected path 对照见 TEMP `evidence/final/final-manifest.json`。Root 的 57 路径保护快照 `evidence/root-nosql-history-protected-20261007.json` 检查为 57/57 匹配；初始 8 个冻结依赖检查为 8/8 匹配。HEAD 与分支没有变化。只写了已授权六路径，无 commit/push。

这份记录是 NoSQL 产品接线包的验收证据，等待 root 独立检查；不代表真实数据库验收，也不代表更大工作目标全部完成。

## Sender identity 与长状态码修复

复核发现，Run 路由曾在 worker busy 检查之前写入面板级 _worker_draft_id。A draft 的 QThread 运行时切到 B 并尝试 Run，忙碌拒绝仍会把这份全局身份改成 B，导致 A 的终态可能更新 B。现在 Redis/Mongo worker 在构造时捕获该次派发的 nosql_draft_id，并由只读属性暴露；Redis 命令和 Mongo Documents、分页、Shell、insert、delete 都把当前 draft id 交给实际 worker。完成与失败槽通过 sender() 读取 worker 身份，并在改动当前结果/status 前核对活动 draft。切换 draft 时 QueryStatusBand 仅保留与活动 draft 匹配的运行状态，避免旧 worker 状态留在 B。Mongo insert/delete 在用户切到其他 draft 后不再清空或重跑那个 draft 的查询。

新增阻塞 fake QThread 回归覆盖 Redis A 完成和失败、Mongo Documents A 完成、Mongo insert/delete A 完成时切到 B；每次都先真实派发 A、等待其进入隔离的 fake worker，再试图在 B 派发或观察终态。B 输入/结果保持，busy B 不启动第二个连接，写操作完成不会启动 B 查询。实际 Mongo Documents、分页、Shell 测试也检查每个 worker 捕获了当前 draft id。未连接真实数据库。

长英文失效码在原布局中超过 status label 的可用宽度。status label 现在启用换行、在下划线处加入零宽断行机会，并在草稿栏和状态栏之间分配更多可用空间；显示文本移除零宽字符后仍完整包含错误码，tooltip 保留原始错误码和完整连接身份。Windows QPA 的实际嵌入 host/workspace 在 960×640、DPR 1.5、Microsoft YaHei UI 21px 下，status 文本需要 400×84、可用内容区为 406×84；新建与历史按钮分别位于 bounds [621,193,127,41] 和 [754,193,206,41]，没有覆盖状态说明。截图与数据见 TEMP evidence/screens/windows-calm-repair/stale_status_manifest.json。这项补图与原来的 8 张图分开保存。

维修后重新运行上述 Windows QPA 整模块命令，14/14 通过、0 skip；新增的普通草稿视觉矩阵另存至 TEMP evidence/screens/windows-calm-repair-ordinary/，没有覆盖原 windows-calm/ 八图。六路径修复后重新逐项核对 root 57 路径快照，57/57 匹配；八个冻结业务依赖也为 8/8 匹配。完整的 repair-before/final 源码 SHA256、逐文件窄 diff、测试日志、前后截图校验信息见 TEMP evidence/repair_final/repair-final-manifest.json 与 repair-delta.diff。本轮仍只写这份报告和原六路径中的五个实现/测试文件；HEAD 与分支保持不变，没有 commit/push。
