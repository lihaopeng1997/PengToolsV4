# 手动 SQL 工作台生产宿主集成

日期：2026-10-02
仓库：`D:/PengTools`
分支 / 基准：`dev/2026-09-23` @ `818fba72bf437de9bd49fcd5b198fb1270096a53`

## 本增量

本包把已有 `ManualSession` 接入生产 `AiWorkbenchPanel` 的 SQL 标签页。每个标签页拥有固定 `ManualSessionTarget` 和独立 owner/session；构造标签页、选择树节点或切换可见标签不打开数据库。SQL 请求经 `ManualQueryHost` 和 provider factory 在线程 owner 中执行，使用当前语句/光标语句入口，不经过可能自动提交的 `run_console_statement`。连接配置、revision、database/schema 和 provider choice 在提交前固定；revision 漂移会拒绝连接并保留原有会话。明文密码只在 driver factory 边界使用。

manual driver 现在复用 `ManualRelationalProviderFactory` 与 `classify_manual_sql`。Fake DBAPI 覆盖 MySQL、Oracle、OceanBase MySQL 和 OceanBase Oracle 的 provider/mode 组合。Oracle 的 schema 使用 raw canonical owner 名称：未引号标识符大写、带引号标识符解码并保留大小写；service/database 值不会被当作 owner。新建 Oracle owner 在用户 SQL 前读取并验证 `SYS_CONTEXT('USERENV','CURRENT_SCHEMA')`；显式 owner 不匹配时执行逐组件转义的 `ALTER SESSION SET CURRENT_SCHEMA` 并再次验证，失败即关闭并拒绝用户 SQL。MySQL target schema 与 database 必须一致。

SQL 编辑器保留现有查询/草稿操作。DML 进入 `DIRTY`，只由用户显式 Commit 或 Rollback 结束；危险 DML 的既有确认与取消零 I/O 路径仍然工作。DDL/DCL 要求 clean 会话并单独确认。语句 effect 由 vendor-aware classifier 表达，`SELECT FOR UPDATE`、`SELECT INTO`、CTE DML、事务控制语句及未知 effect 不依赖 `is_read` 猜测。例程块不会被盲目按分号拆分；不支持的拆分会拒绝执行。

查询结果通过 `ManualResultTableModel`（`QAbstractTableModel`）和 `QTableView` 呈现，typed pages 由模型直接持有；不再把每个单元格复制成 `QTableWidgetItem`。UserRole 保留原值，显示 delegate 单独格式化；分页追加、失败保留最后一次成功结果，以及 `NULL` 和空字符串区分均有 fake 页面回归。

Agent 继续使用现有 `DataCenterAgentController` / `DataCenterAgentPanel`，与 manual session 完全分开。configured model 与 connection snapshot 固定后由 worker 装配；text-only draft 保留旧草稿入口并证明零数据库访问。目标显示语言变更不重绑 snapshot；draft 固定文案随语言重绘，query connection label 原样保留。Redis TEMP 回归使用与 draft marker 同名的 captured query target，切换 zh/en 后验证 connection ID、revision、目标文本不变，且没有 Redis driver 或模型请求。

关闭手动会话有公开的 snapshot、gate 和五态非阻塞接口：`ready`、`ready_to_close`、`pending`、`cancelled`、`decision_required`。预检只读；跨 host 先聚合 snapshot 再决定，`UNKNOWN` 阻止全局 commit；用户取消关闭保留 session。混合 dirty/unknown 不能先提交一部分，busy owner 保留引用直到真实 cleanup 完成。该接口不等价于旧 Ai 工作台的后台 metadata/test worker 退出清理；`workbench_shutdown_cleanup()` 与整应用最终退出联合验收仍 pending。

compact editor overflow 提供 Format、Clear，以及用途明确的“SQL 草稿与对象详情”侧栏切换；旧 AI 自然语言草稿与对象详情仍可访问。真实 `DataCenterPanel` + 默认 SQL host 的 offscreen 几何回归覆盖 zh/en × 960×640/1160×720，并在侧栏打开后关闭，再校验 Run、New Tab、Commit、Rollback、Stop 按钮完整、typed 结果仍处于 viewport 内。四张截图写入 TEMP `C:/Users/Lenovo/AppData/Local/Temp/pengtools-manual-lifecycle/`：

- `data-center-zh-960x640.png`
- `data-center-zh-1160x720.png`
- `data-center-en-960x640.png`
- `data-center-en-1160x720.png`

这是自动化几何证据，不构成用户视觉签收；没有操作运行中的 UI7。独立 UI 审阅仍发现 Agent header 有重叠，归 AgentPanel 写者继续修复；因此本包的 Run/事务按钮和 typed viewport 四格通过不代表整个工作区视觉已经接受。

## 验证

以下行为测试使用 TEMP profile/model store、fake DBAPI/driver 和 fake model transport。没有连接真实数据库、Redis、MongoDB、内网模型或网络服务，也没有读写用户 data/config/credentials/session。

| PowerShell 命令 | 结果 |
|---|---|
| `$env:QT_QPA_PLATFORM='offscreen'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_manual_integration tests.test_data_center_workbench_integration tests.test_data_center_manual_relational_providers -v` | exit 0；44 tests |
| `$env:QT_QPA_PLATFORM='offscreen'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_workspace.DataCenterWorkspaceTests.test_actual_default_host_compact_layout_keeps_agent_and_typed_result_reachable -v` | exit 0；1 actual-host test，四种尺寸/语言布局和侧栏开合均通过 |
| `$env:QT_QPA_PLATFORM='offscreen'; .\.venv-build\Scripts\python.exe -m unittest tests.test_data_center_agent_layout -v` | exit 0；3 tests |
| `.\.venv-build\Scripts\python.exe -m py_compile ui/data_center/integration.py tests/test_data_center_workbench_integration.py panels/ai_workbench_panel.py tests/test_data_center_manual_integration.py` | exit 0 |

上述组合的关键覆盖包括：TEMP MySQL panel 的 SELECT→typed result 和 DML `DIRTY`→显式 commit→`CLEAN`；每个 SQL Tab 的 owner 隔离；dirty/unknown close 预检、取消与 cleanup；Oracle 默认登录 owner、quoted owner 及失败 fail-closed；provider revision/schema drift 在 connector 前拒绝；Agent query evidence 和 SQL 回填目标校验；draft 零连接；显式 target 切换、Tab 可见性变化、模型/连接 revision 变化；以及菜单文案和真实宿主布局。此前冻结的底层 evidence 见同目录 `manual-session.md`、`manual-providers.md` 和 `changeset-dialects.md`，不在本回合重复重跑。

## 限制和后续

- 生产 DBAPI、真实 MySQL/Oracle/OceanBase 实例、Redis/Mongo 实例与内网模型均未联调。provider matrix 是 injected fake evidence。
- Dameng 当前 manual provider 明确 `MANUAL_PROVIDER_UNSUPPORTED`；不能把六引擎 Agent 装配证据表述成六引擎手动事务兼容。TLS/SSH/DSN 等现有 connector 不支持的 profile 选项仍 fail closed。
- 当前没有 driver-native interrupt，也未证明总语句 deadline；阻塞调用必须保持 `pending`，不强杀线程。connector timeout 不等于总操作 deadline。
- Oracle `CURRENT_SCHEMA` 的 SQL 行为由 in-memory fake 覆盖；没有真实 Oracle quoted-user/权限联调。官方语义参考见 [Oracle ALTER SESSION](https://docs.oracle.com/en/database/oracle/oracle-database/21/sqlrf/ALTER-SESSION.html)、[SYS_CONTEXT](https://docs.oracle.com/en/database/oracle/oracle-database/21/sqlrf/SYS_CONTEXT.html) 和 [Oracle object names](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/Database-Object-Names-and-Qualifiers.html)。
- DataCenterPanel/MainWindow 的退出事务协调器虽有独立接口与 fake 聚合证据，Ai 旧 metadata/test workers 的非阻塞 cleanup 接缝以及整应用真实退出仍待后续联合验收；NoSQL 旧 worker 也未纳入本包。
- ChangeSet 方言底座已另包实现，但本次没有生产表格编辑器、历史/收藏、导入/导出、execute-all/procedure-block 工作流或兼容迁移 UI。完整 DC00–DC06、对象树/历史/SSH/TLS、真实联调、视觉签收和发布仍未完成。

## 冻结源文件 SHA-256

以下哈希在本报告写入前生成，供主代理独立抽验；当前不暂存、提交或推送。

| 文件 | SHA-256 |
|---|---|
| `panels/ai_workbench_panel.py` | `9E9811754D21E11ABA139F2CBC15857569ACEC513A6E75247BED9A579D94E65C` |
| `tools/data_center/manual_driver.py` | `620CFF5866269452B87A79153689BAE8F79BE726B0545F7653AC0BFB7D013575` |
| `ui/data_center/manual_query.py` | `EACEBA78434E8DE6D7F5AC2C5678EFE6361A47D37A1E6714183F02A86FEB222F` |
| `ui/data_center/integration.py` | `AF68F31CFFE4F9A510AA4B295C4E9AF842AF8938A1214626E9E87551FBC76D28` |
| `ui/data_center/result_model.py` | `CBFAE113A5861EA7E49D8E265CC707DB3B7DC345753C89F357F871A59D0B9C30` |
| `tests/test_data_center_manual_integration.py` | `2D70C00A937377E9C5098483933D4FD6C6385E7FF900E236D50733EEEDEB94EB` |
| `tests/test_data_center_workbench_integration.py` | `2C272D7643EE4E0AC2A27E095104E5FAD1DCDF24065EDD61160C42124D6D8511` |

The actual-host geometry fixture in `tests/test_data_center_workspace.py` is owned by the UI worker; the exercised version for this run had SHA-256 `5801841140A2AD4D24CF8E23091FB275EF67A81D9C05B5E9F47869D7C329A003`. It is evidence provenance only, not part of this writer's freeze set.
