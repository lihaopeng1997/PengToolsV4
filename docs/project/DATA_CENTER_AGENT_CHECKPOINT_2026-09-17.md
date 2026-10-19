# 数据中心 Agent：关系库专用连接器检查点（2026-09-17）

状态：用户恢复任务后，Luna Max 已完成续修，主代理已通过代码复核及 55 项定向模拟验证；本轮改动仍未提交或推送。真实数据库、达梦专用连接器及完整 Agent 尚未验收。需求正文仍为 [DATA_CENTER_AGENT_REQUIREMENTS.md](DATA_CENTER_AGENT_REQUIREMENTS.md)，历史宿主检查点见 [2026-09-16 记录](DATA_CENTER_AGENT_CHECKPOINT_2026-09-16.md)。

## 基线与工作区保护

- 仓库：`D:/PengTools`，远端 `https://github.com/lihaopeng1997/PengToolsV4.git`。
- 实际分支：`ui/prism-v1`；本轮起点完整 SHA：`ce01d8166ed94f411bbd4d917cff938dc887fbc8`。
- 2026-09-17 首次 fetch 遇到 TLS EOF；使用单次 `http.sslBackend=schannel` 后 fetch 成功。此时 `origin/ui/prism-v1` 与 HEAD 相同，双方独有提交 `0/0`；未修改持久 Git 配置。
- 用户授权继续实现，明确要求保留遗留未提交文件，其处理方式尚待确认。本轮不暂存、提交、删除或覆盖这些文件，不为获得干净状态执行 stash、clean、reset 或切换分支。
- Luna Max 负责实现和定向自测，主代理负责需求边界、差异审查、独立验证和交接。

起始遗留文件共 13 个，已记录内容哈希供交付前核对：

| 起始状态 | 路径 |
|---|---|
| 已修改 | `docs/project/DEVELOPMENT_AND_AI.md` |
| 已修改 | `docs/project/README.md` |
| 已修改 | `resources/build_info.json` |
| 未跟踪 | `docs/project/AI_CONTEXT_WORKFLOW.md` |
| 未跟踪 | `docs/ui/prism-implementation-2026-09-15/LUNA_TABS_GAP.md` |
| 未跟踪 | `docs/ui/prism-implementation-2026-09/LUNA_MENU_GAP.md` |
| 未跟踪 | `docs/ui/prism-implementation-2026-09/LUNA_TOPBAR_GAP.md` |
| 未跟踪 | `scripts/diagnostics/prism_interface_dialog_preview.py` |
| 未跟踪 | `scripts/p09a_fix1_report.txt` |
| 未跟踪 | `scripts/p09a_fix2_report.txt` |
| 未跟踪 | `scripts/p09b_fix1_report.txt` |
| 未跟踪 | `scripts/p09b_fix2_report.txt` |
| 未跟踪 | `scripts/p09b_report.txt` |

## 本轮范围与验收标准

1. 专用关系库连接器在调用驱动建连前传入经过核对的超时参数，不复用旧手动连接或 `db_connect.open_connection`。
2. 连接由 worker 独占；在查询前完成只读初始化，失败清理独立连接；不向模型开放事务控制，不执行 commit。
3. 超时参数固定于宿主，不能由模型或 profile 覆盖；非法值、未知组合及无法保留的 TLS/SSH/目标语义在联网前拒绝，禁止静默回退驱动或关闭证书校验。
4. 导入模块不加载真实驱动、Qt 或用户配置；测试仅使用内存 profile、假驱动和模拟数据。
5. 文档区分驱动建连/读写超时、协作取消与查询总 deadline；不把单阶段超时描述为整个操作的硬时限。
6. 不修改旧工作台、UI、用户数据、凭据、启动/打包或 SSH 会话；不接入真实模型或数据库，不把模拟通过标为真实兼容验收。

本轮前置基线验证：`python -m unittest tests.test_data_center_host_relational tests.test_data_center_readonly_lease tests.test_data_center_package_surface -q`，23 项通过，退出码 0。实现后证据另行记录，不沿用该数字作为本轮变更验收结果。

## 中断时的候选改动与首轮复核（历史）

Luna Max 已留下以下文件，尚未提交、推送或完成实施者自测回执：

- `tools/data_center/readonly_relational_connectors.py`：候选专用连接器。Oracle 使用结构化地址和 `tcp_connect_timeout`/零重试；MySQL 与 OceanBase MySQL 传入连接/读取超时；OceanBase Oracle 使用 ODBC 登录超时和原始连接整数查询超时；达梦明确拒绝。
- `tools/data_center/host_relational.py`：默认入口已改为懒加载专用连接器。因此工作树行为已不同于已发布 HEAD，不能继续假设默认入口一定拒绝联网。
- `tools/data_center/readonly_driver_adapters.py`：移除未经核实的 MySQL/ODBC 查询 deadline 属性探测；当前驱动配置超时与查询 deadline 是不同能力。
- `tests/test_data_center_readonly_relational_connectors.py`：8 项假驱动行为测试。

主代理已运行：`python -m unittest tests.test_data_center_readonly_relational_connectors tests.test_data_center_package_surface tests.test_architecture_boundaries -v`，14 项通过，退出码 0。`git diff --check` 通过。没有真实数据库、模型、UI 或发布验证。原有 13 个未提交文件逐项 SHA-256 比较全部相同。

当时尚未修复的审查项（已由下节续修解决）：

1. **Oracle 读取超时缺失**：入口验证了 `read_timeout`，但 Oracle 分支未接收或设置 `call_timeout`；连接后的只读初始化仍可能无读取时限。应在返回连接、创建初始化游标前设置并核验该属性，失败关闭连接，增加顺序与清理测试。
2. **ODBC 密码被裁剪**：`_escape_odbc()` 经 `_text()` 执行 `strip()`，会改变合法的首尾空格密码。转义必须保留原值，增加包含空格、分号、右花括号的行为回归。
3. **旧测试需显式隔离**：`test_default_legacy_connector_fails_closed` 仍使用未打桩的默认 Oracle 入口。新默认路径可能加载用户 Oracle 设置并尝试真实连接；不得直接运行该旧用例或包含它的完整宿主测试，先改为假驱动/禁止联网边界。
4. **ODBC 驱动选择**：没有精确驱动名时，候选代码会选择首个模糊匹配项；应保留旧行为对多个候选驱动的歧义拒绝，不随枚举顺序选择驱动。
5. 清理本轮无必要的公共别名，修正宿主仍称“旧连接器被禁用”的过期注释；补非法超时、传输选项拒绝、初始化失败清理、凭据错误脱敏及新模块独立导入测试。

中断时用户选择保留现场等待 Luna Max，后续又明确要求继续；由原 Luna Max 代理恢复实施，没有改为主代理编码。本轮没有发布授权，也没有以处理未知归属文件来满足干净工作区要求。

## 恢复后的实施与验收回执

- Luna Max 修复以上五项审查问题，另限制专用连接器超时为正有限秒数且不超过 86400 秒，在驱动导入和建连前拒绝超大值；MySQL 发送阶段使用与读取阶段相同的固定 `write_timeout`。
- Oracle 在建连后、创建任何只读初始化游标前设置并核验 `call_timeout`（毫秒）。属性不支持或初始化失败时关闭独立连接；假宿主用例检查执行顺序和错误脱敏。
- ODBC 密码转义保留首尾空格及分号/花括号；包装层转发属性赋值到原始连接。驱动只选择精确名或唯一模糊匹配，多个候选明确拒绝。
- 旧默认入口测试改为内存 profile、假 PyMySQL 和禁止 socket 建连的边界；新模块导入 smoke 不加载 Qt、数据库驱动或旧配置模块。移除本轮新增的兼容别名。

| 组合 | 本轮实现 | 限制/真实验收 |
|---|---|---|
| Oracle | 结构化 host/port/service，连接前 TCP 超时、零额外重试；初始化前 call_timeout | 原始 DSN/描述符拒绝；Thin/Thick、实际超时及只读保护待测试库验证 |
| MySQL / OceanBase MySQL | PyMySQL 建连前 connect/read/write_timeout，autocommit=False，local_infile=False | connect_timeout 至少 1 秒；固定 socket 时限不等于查询总 deadline；真实引擎待验收 |
| OceanBase Oracle | ODBC 登录超时整数秒；创建游标前原始连接查询超时整数秒，向上取整 | 需既有 ODBC Schema 确认标记；真实驱动执行超时及只读事务待验收 |
| 达梦 | 默认明确拒绝建连 | dm_svc.conf 覆盖及目标重映射契约未解决，不能标为已支持 |
| 显式 TLS/SSH/原始连接串选项 | 非空受限选项在联网前拒绝 | 本轮未实现这些传输配置，禁止静默忽略；后续另按需求完成 |

超时不是整次建连/登录/查询的硬总时限；DNS、多地址、认证及厂商实际执行行为仍有独立限制。MySQL/ODBC 不再报告未经验证的动态 deadline 属性支持，驱动 cancel 仍未开放。宿主/lease 当前将连接错误折叠为安全的 CONNECT_FAILED；具体专用连接器拒绝原因可在直接连接器边界识别，不泄露 vendor 文本。

### 修改与验证

本轮代码/测试：`tools/data_center/readonly_relational_connectors.py`、`host_relational.py`、`readonly_driver_adapters.py`；`tests/test_data_center_readonly_relational_connectors.py`、`test_data_center_host_relational.py`。文档：本检查点、2026-09-16 检查点续接链接及 `DATA_CENTER_AGENT_DEVELOPMENT.md`。

Luna：关系库宿主、lease/矩阵、连接器、包导入与架构联合测试 45 项通过，退出码 0；编译与 diff 检查通过。主代理独立验证：

`python -m unittest tests.test_data_center_host_relational tests.test_data_center_readonly_lease tests.test_data_center_readonly_lease_matrix tests.test_data_center_readonly_relational_connectors tests.test_data_center_readonly_integration tests.test_data_center_host_integration tests.test_data_center_package_surface tests.test_architecture_boundaries -q`

55 项通过，退出码 0，含假模型/关系库闭环；`git diff --check` 通过。全部为内存 profile/假驱动/导入 smoke，没有真实模型、真实数据库、实际窗口或发布包证据。原有 13 个遗留文件逐项内容哈希相同。分支和 HEAD 不变，无暂存、提交或推送；工作树包含本轮未提交改动及原遗留文件。

下一步：继续固定目标的元数据工具与宿主运行入口；在专门测试库完成各引擎只读、超时、TLS/目标身份验证，解决达梦配置覆盖契约后再开放该组合。Qt 事件面板、手动工作台/事务、SSH、迁移和打包仍按原需求待办。发布前需单独确认本轮文件提交范围及遗留文件处理，不得整体暂存工作树。

## 元数据工具与纯 Python 宿主入口（模拟验收通过，未发布）

用户已要求实施后续规划，立即开展第 1 包，由原 Luna Max 实施。基线仍为 `ce01d8166ed94f411bbd4d917cff938dc887fbc8`，新一轮 fetch 成功后远端一致。保留前包改动及原有 13 个遗留文件，无提交/推送。

- 目标：固定目标的 `search_objects/describe_object`，对象和游标绑定连接版本及数据库/Schema；元数据采用可信参数化模板并在独立 worker 内建连、使用、关闭。
- 宿主组合：现有模型宿主、运行器、关系库查询、NoSQL 路由与元数据服务；查询/草稿意图区分，草稿不建连；工具结果统一有界脱敏，元数据和采样也计入回传预算。
- 验收：假模型“搜索→结构→查询→基于证据回答”，并覆盖歧义、目标变化、非法对象/游标、超时/取消、敏感字段、截断及禁止操作零驱动调用。
- 边界：不改主窗口、既有手动工作台、真实配置/数据或依赖；不自动访问真实模型/数据库。用户已明确暂无测试环境，先完成模拟与界面准备，无环境不替代真实验收。
- 界面准备由独立 Luna Max 负责新增原生 Agent 面板原型、离屏测试及模拟预览；不接主窗口、不改旧面板，实际用户视觉接受仍待确认。
- 新增目录模板依据：[Oracle ALL_OBJECTS](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_OBJECTS.html)、[ALL_TAB_COLUMNS](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_TAB_COLUMNS.html)、[ALL_COL_COMMENTS](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_COL_COMMENTS.html)、[MySQL TABLES](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)、[COLUMNS](https://dev.mysql.com/doc/refman/8.4/en/information-schema-columns-table.html)。Oracle 旧版本分页须内层排序、ROWNUM 上限再外层 offset，不能直接使用 ROWNUM > 正整数。
- OceanBase Oracle 需保留真实兼容缺口：列注释文档存在 OBJECT_NAME/TABLE_NAME 差异，本轮不假设 Oracle 注释 join 通用；当前共享初始化语句 SET TRANSACTION READ ONLY 未获得模式专属兼容证明，官方 [Oracle 模式事务控制](https://en.oceanbase.com/docs/enterprise-oceanbase-database-en-10000000000851313) 展示 START TRANSACTION READ ONLY。不据此擅自修改前包策略，实际初始化失败必须拒绝查询；不能仅凭假驱动宣称支持真实 OceanBase Oracle。

### 本包实现与验收回执

新增 `tools/data_center/host_metadata.py` 和 `host_runtime.py`。目录模板为不可变映射，lease/adapter 新增宿主专用 `execute_metadata(template_id)`，拒绝未知、跨引擎及原始 SQL 模板 ID，普通 SQL 仍受原只读策略约束。Oracle/OceanBase Oracle 使用数字绑定和有序嵌套 ROWNUM 分页；OceanBase 不假设列注释兼容。元数据 worker 拥有独立资源，Redis 分页保留同一 worker lease；Mongo 集合发现限于宿主允许列表，采样结构按推断处理。

运行入口绑定模型与目标身份，检查当前 profile 版本；对象和游标失效后拒绝使用。SQL 作用域检查解析物理表与 CTE 来源，要求显式允许的 Schema，拒绝跨库、跨 Schema 及数据库链接。草稿模式不构造数据库资源。工具结果投影保留对象/游标协议形状、限制数据并脱敏；注释按不可信数据处理。运行剩余预算传播至工具 deadline，不能重新给予已消耗预算完整时长。超时或停止后结果不作为成功证据，底层阻塞操作仍受驱动能力限制，不承诺强制中止网络调用。

最小现有文件修改：`agent.py` 将元数据计入数据调用预算、仅成功数据查询验证最终回答；`agent_query_tool.py` 传递宿主 deadline；`readonly_lease.py`、`readonly_driver_adapters.py` 增加固定目录模板通道。新增测试：`test_data_center_metadata_runtime.py`、`test_data_center_metadata_driver_boundary.py`、`test_data_center_agent_evidence.py`。这些是本地未提交改动，不能通过远端 HEAD 审查。

界面准备新增 `ui/data_center/agent_panel.py`、包初始化、`tests/test_data_center_agent_panel.py` 和 `scripts/diagnostics/data_center_agent_preview.py`。原生模拟面板提供固定目标/模型、阶段、摘要、正文、工具证据、停止及复制/手动转交信号。Qt 队列按 run、Tab、事件序号及代际过滤迟到事件，停止先显示请求状态，终态后不接受正文追加；展示截断不改变执行参数或预算。未接主窗口、未提供真实发送闭环，不宣称第 3 包完整验收。

主代理独立运行：

`python -m unittest tests.test_data_center_metadata_runtime tests.test_data_center_metadata_driver_boundary tests.test_data_center_agent_evidence tests.test_data_center_agent_runner tests.test_data_center_agent_policy tests.test_data_center_readonly_lease tests.test_data_center_readonly_lease_matrix tests.test_data_center_readonly_integration tests.test_data_center_host_integration tests.test_data_center_host_nosql tests.test_data_center_nosql_integration tests.test_data_center_host_relational tests.test_data_center_readonly_relational_connectors tests.test_data_center_agent_panel tests.test_data_center_package_surface tests.test_architecture_boundaries -q`

147 项通过，退出码 0。包含假模型搜索→描述→查询→基于成功证据回答、作用域拒绝零查询、草稿零数据库构造、版本变化、受控模板、取消/期限、脱敏和事件隔离。全部为内存/假驱动/模拟模型或离屏 Qt，没有真实模型、数据库及发布证据。独立 panel+架构测试 16 项通过；主代理查看模拟图片，最终窄栏 PNG 实际尺寸 `(340, 900)`，不能用源码测试替代用户实际窗口视觉验收。预览保存在系统临时目录 `pengtools_data_center_agent_preview_yygh3gfu`，不是打包资源。

后续顺序：具备专用环境后开展第 2 包真实兼容；随后接真实 Qt 发送/事件闭环并做实际窗口验收。统一工作台先提交可查看的原生原型供用户确认，手动事务/ChangeSet、SSH、迁移及发布另按规划继续。没有测试环境时不使用生产库补证据；本轮不授权整体暂存、提交或推送。网页接手限制已同步至 `GIT_WEB_HANDOFF_2026-09-16.md`。

交付前复查：相关 Python 文件编译及 `git diff --check` 通过；原有 13 个遗留文件逐项 SHA-256 与起点一致，暂存区为空。当前分支/HEAD 不变，与本轮已 fetch 的 `origin/ui/prism-v1` 差异 `0/0`，没有提交或推送。全部实施代理已结束编辑，当前保留可审查现场。

## 驱动契约依据与限制（前包）

2026-09-17 由 Sol Max 独立核对公开厂商文档；只做驱动契约研究，没有读取或审查本地未提交源码，也没有连接数据库。

- Oracle：`tcp_connect_timeout` 为浮点秒，`retry_count=0` 禁止额外重试。原始 DSN 可能优先于独立参数，Thick 模式默认还会原样传递 DSN；不能简单追加 timeout 就认为受控。应使用受控 `ConnectParams`，不再同时传回原始 DSN。该时限是每地址的传输建立限制，不是 DNS、登录、全部地址或整次查询的总硬时限。依据：[ConnectParams](https://python-oracledb.readthedocs.io/en/stable/api_manual/connect_params.html)、[参数优先级](https://python-oracledb.readthedocs.io/en/v3.2.0/api_manual/module.html)、[Thick DSN 透传](https://python-oracledb.readthedocs.io/en/stable/api_manual/defaults.html#oracledb.Defaults.thick_mode_dsn_passthrough)。
- ODBC：`pyodbc.connect(timeout=...)` 是连接前登录超时，单位整数秒；连接的 `timeout` 是查询超时，应在创建游标前设置，是否执行由实际驱动决定。`SQL_MODE_READ_ONLY` 仅为提示，不能替代只读事务或账号权限。依据：[connect](https://github.com/mkleehammer/pyodbc/wiki/The-pyodbc-Module)、[Connection.timeout](https://github.com/mkleehammer/pyodbc/wiki/Connection)、[ODBC 访问模式语义](https://learn.microsoft.com/en-us/sql/odbc/reference/syntax/sqlsetconnectattr-function?view=sql-server-ver17)。
- 达梦：并非没有超时 API。官方文档提供 `login_timeout`（毫秒）及 `connection_timeout`（执行超时，秒），但服务配置优先于 API；`dmsvc_path` 不保证禁用全局配置，DPI 配置还支持 `ADDRESS_REMAP`。当前没有经过验证的配置隔离路径，无法保证宿主固定的超时与目标不被覆盖，因此本轮默认专用连接器保持不可用，后续需补专门环境验证。依据：[dmPython API](https://eco.dameng.com/document/dm/zh-cn/pm/dmpython-interface)、[服务名与直接地址示例](https://eco.dameng.com/document/dm/zh-cn/faq/go-Python-new.html)、[DPI 配置及优先级](https://eco.dameng.com/document/dm/zh-cn/pm/physical-storage.html)。

## 2026-09-20 续接：Qt 输入接缝与当前证据

- 当前实际分支为 `ui/prism-v1`，本地 HEAD 与 `origin/ui/prism-v1` 均为 `818fba72bf437de9bd49fcd5b198fb1270096a53`。工作树仍包含本轮开发现场及起始 13 个遗留文件；未暂存、提交、推送、删除或整体覆盖。
- 主代理重新执行元数据、关系库/NoSQL 宿主、模型适配、证据与 Qt 面板等 9 个聚焦模块，共 95 项模拟测试通过。该数字是本次续接的较小验证集，不替代此前 147 项联合记录，也不构成真实模型或真实数据库兼容证明。
- `ui/data_center/agent_panel.py` 已增加多行提问输入和发送信号。空白、运行中及取消待确认状态不能发送；宿主明确接收后才清空原提示，新建对话会使旧代际事件失效。面板仍是仅接收注入事件的界面接缝，不读取配置、不创建模型或数据库连接。
- 主代理独立执行 `python -m unittest tests.test_data_center_agent_panel -v`，18 项通过；相关文件编译通过。诊断脚本仅用合成事件渲染 `1440x900`、`960x640`、`340x900` 三种尺寸，主代理检查未见横向溢出，输入、发送、停止和新建对话控件可见。该离屏检查不是主窗口视觉验收。
- 已新增一次性 `AgentRunSnapshot` 和可注入的 Qt 运行控制器。runtime/factory 仅在后台线程运行，事件经代际绑定 facade 返回；支持单运行互斥、停止/新会话/关闭取消、错误脱敏和确定性清理。草稿仍调用注入的 draft runtime 生成内容，数据库零连接由既有 pure-Python draft 契约保证；控制器自身不构造配置、模型或数据库资源。
- 主代理退回并复核了草稿误拒绝、错误作用域终态、异常文本泄露和相同 run ID 复用问题。后续集成审计又要求补充 `cleanup_finished` 与幂等 `dispose()`：旧控制器会先断开面板信号、取消并等待 worker，在清空线程/worker/token 引用后才报告清理完成，避免换连接、新建对话和关闭模块时残留监听或线程。最终独立执行 controller + panel 联合测试 30 项通过（controller 12、panel 18），编译和 diff 检查通过。控制器仍未连接真实配置、模型或数据库。
- 当前生产数据中心仍使用既有手动工作台/AI SQL 区域；新 Agent 面板与控制器尚未挂载主窗口。下一步先完成现有关系库、Redis、Mongo 工作台及配置装配的只读集成审计，再确定最小挂载点和生命周期边界；旧手动工作台保持可用。
- 用户暂无专门测试库和可用内网模型测试配置。真实兼容、达梦、主窗口整合、视觉验收和发布继续标记为待验收，不能使用生产环境补证据。
