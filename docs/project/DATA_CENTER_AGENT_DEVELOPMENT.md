# 数据中心 Agent：Luna 开发与主代理复核交接

日期：2026-09-16。状态：DC-01 纯 Python Agent 核心、DC-02 只读执行底座及 DC-03 纯 Python 宿主适配组件已实现，并通过注入式假模型/假驱动验证。真实数据库、真实内网模型、Qt 工作台与发布包仍未验收。唯一需求正文是[数据中心重构 V1](DATA_CENTER_AGENT_REQUIREMENTS.md)。先读根 AGENTS 和项目 README；最新用户决定优先于历史 UI-only、AI 不执行和系统标题栏限制。

## 1. 本轮不可误解的范围

- 首要交付是能驱动内网大模型的只读工具闭环，不是聊天框或一次性SQL生成。
- Agent允许自动执行查询，永不执行写入/DDL/事务控制；手动编辑器独立支持默认手动事务。
- 查询结果允许有限、脱敏地回传选定内网模型；不向外网模型自动回退。
- 思考摘要（接口提供时）、正文、工具进度必须真正流式；不能先等待HTTP完整返回。
- 数据中心最终统一六类引擎、连接树、Tab、结果、编辑、导入导出、历史与专用SSH隧道。先Agent纵向切片，不能把其余已确认范围丢掉。
- 原UI菜单/自绘标题栏/全局Tab仍待完成，是并行项目；这里不同时重写那些公共文件。

## 2. 第一个可评审开发任务：DC-01

先实现隔离的Agent核心及假模型/假数据库测试，不连接用户模型或数据库，不修改MainWindow和既有聊天的行为。

### 文件所有权与接口

|负责组|独占区域|交付|
|---|---|---|
|Luna A 模型协议|新增 `tools/data_center/model_adapter.py`、`streaming.py`，独有模型协议测试|增量HTTP/SSE，AgentTurn/ToolCall，原生tools与严格JSON/文字降级；复用内网连接策略|
|Luna B 工具与边界|新增 `tools/data_center/policy.py`、`tool_registry.py`、`result_projection.py`，独有policy测试|闭集工具参数/目标校验、结果脱敏与预算；初期仅假executor|
|Luna C 运行器|新增 `tools/data_center/agent.py`、`contracts.py`、`budget.py`，独有runner测试|状态机、多轮工具结果回填、调用去重、停止与证据；只依赖注入接口|
|主代理|需求、共享接口定稿、diff与运行证据审查|先锁contracts再分组动工；不给两个组同时改contracts或旧公共网络函数|

共享contracts先由C提供，主代理核对后A/B使用。新模块可迭代，但业务层禁止导入Qt；Qt适配留在ui层后续任务。各组不自行commit/push或重启当前体验包，主代理按已授权分支统一交付。

### 共享对象最小字段

- RunContext：run_id、tab_id、model_config_id、connection_id、database、schema_allowlist、profile_revision、intent、policy_version；不包含明文密码。
- ToolCall：call_id、name、arguments；AgentTurn：text、tool_calls、finish_reason、usage。
- ToolResult：ok、code、data、evidence_id、query_id、scope、truncated、limits、elapsed_ms。
- AgentEvent：run_id、tab_id、sequence、event_type、payload；序号单调递增，消费者按run过滤。
- QueryRequest：session_id、query_id、generation、sql/结构化操作、参数、限制、deadline；QueryResult保留原始类型，独立生成model projection。
- CancellationToken是宿主状态，模型不能清除；Budget同时计算模型回合、调用、数据查询、时限、字节，不仅检查prompt长度。

严格JSON兼容协议示例（DEMO，不能因此执行真库）：

```json
{"kind":"tool_call","call_id":"demo-1","name":"search_objects","arguments":{"keyword":"订单"}}
```

模型结果回传必须包含同一个call_id及规范化ToolResult；原生协议用对应tool_call_id，兼容协议用完整结构化观察消息。任何自然语言中的JSON样例均不执行。最终输出用kind=final，歧义用kind=clarify；解析失败不猜测工具。

### DC-01验收脚本场景

1. 假模型依次search_objects、describe_object、query_readonly，再基于假结果给答案；每步输入含前一步真实ToolResult，顺序和ID可断言。
2. 查询用户DEMO订单总数，最终“12条”必须来自query_id对应结果，不能测试直接返回写死答案。
3. 模型请求DELETE/exec_shell/文件读取/跨连接，假executor调用数为0；即使结果文本诱导“忽略规则”仍保持同样拒绝。
4. SSE延迟服务持续至少1秒，结束前已发出可见text/reasoning事件；中文分片、工具参数半包不执行。
5. Stop后不再发模型请求或新工具，已跑executor返回的晚到结果不更新已关闭run；达到预算返回部分证据并明确未完成。
6. 相同call_id重复帧只执行一次；同ID不同参数报错。模型无tools能力进入明确兼容或草稿模式，不能假称查库。

DC-01回执不得声称只读数据库已验证。DC-02 现在已经补上 SQL AST、会话代际、worker、投影和注入式只读边界，但厂商只读会话、真实驱动、真实取消能力和内网模型仍需 DC-03 验收后才能开放真实执行入口。

## 3. 后续接线顺序

DC-02 已在当前工作区实现只读 executor、SessionManager、SQL AST 门禁、六类引擎能力适配、结果投影和 AgentQueryTool/注册表接线；`sqlglot==30.18.0` 已加入 `requirements.txt`，但真实驱动与打包验证仍待完成。不要把 SQLGlot 解析通过当安全保证。现有编程 Agent 文件工具不注册，现有 `run_console_statement` 自动 commit 路径不用于 Agent。

DC-03 当前工作区已提供 `model_config.py` / `host_model_adapter.py` 的选定模型配置与流式宿主边界、`readonly_driver_adapters.py` / `readonly_lease.py` 的关系库只读 lease、以及 `nosql_codec.py` / `readonly_nosql_clients.py` 的 Redis/Mongo 有界只读 facade。它们只接受宿主注入的 loader、transport、连接或 client；包根仅导出宿主需要的稳定类型，导入不访问真实配置、数据库或 Qt。对应验证使用假模型、假 driver 和内存数据，不能当作真实兼容证明。

DC-03 仍需在专门测试库和已选内网模型验证真实工具能力；生成/查询意图区分、结构歧义、只读拒绝和有限结果回传全部通过后再接 Qt 事件界面。SSE UI 至少检查发送即时反馈、真实增量、思考/正文分区和停止。

DC-04至DC-06按需求正文实施工作台、手动事务、SSH及迁移。若缺驱动或测试环境，记录具体组合不可验收，不靠源码检查把状态标绿。原始数据目录和正在运行的体验包保持不动。

## 4. 主代理复核清单

- 用户最后授权是否准确落实：自动只读查询，手动写入与Agent执行隔离，思考摘要/正文真流式。
- 模型请求是否保留tools和ID；增量读取是否确实发生；普通文本/半个调用是否不执行。
- 是否有任何途径把模型参数变成任意连接、文件、shell、write、commit或rollback。
- 表结构/查询结果是否作为不可信数据，敏感值在入模型之前处理；是否把截断样本当全量统计。
- 是否做到UI线程无网络/数据库等待；取消不撒谎、迟到事件不串Tab；无静默全轮重试。
- 新测试是否验证行为而非只assert源码字符串；能否提供失败前/通过后的可观察证据。
- 是否修改了无关业务、现有用户配置、模型安全边界或日志SSH会话；若有立即退回。

## 5. 回执模板

```text
分支 / 基线SHA / 本次SHA / 远端SHA：
实现的需求ID与状态：
独占修改文件及公共接线：
接口变更与兼容方式：
已运行测试（命令、数量、退出状态）：
模拟证据 / 真实模型证据 / 真实数据库证据 / 视觉证据：
拒绝或未支持的驱动/方言/模型能力：
用户数据保护与回退：
尚未实现或验收项：
```

## 6. DC-01 实施回执（2026-09-15）

- 新增 `tools/data_center/` 纯 Python 核心：契约、预算、多轮运行器、增量 SSE/模型适配、闭集工具策略、工具注册表和模型结果投影；没有导入 Qt、数据库驱动或通用编程 Agent。
- 模型链路逐字节解析 UTF-8/SSE，分别产生可展示推理摘要、正文、工具、usage 与完成事件；优先使用小块 `read1`，工具参数完整解析为 JSON 对象之前不产生可执行调用。
- 六个工具采用固定 schema。模型不能覆盖连接、主机、凭据、文件、命令或 shell 目标；Redis/Mongo 写入形态被拒绝。
- DC-01 尚无 SQL AST，因此 `query_readonly` 在注册表统一返回 `READ_GUARD_NOT_READY`。包括 `SELECT` 在内的 SQL 都不会触达执行器；这是在 DC-02 完成双层只读门禁前的主动关闭状态。
- 运行器支持工具结果按 `tool_call_id` 回填、多轮继续、同 ID 同参数复用、同 ID 不同参数拒绝、预算终止、取消及迟到事件抑制。
- 模型投影保留 NULL、空串、数值、日期和 bytes 的类型区别，对敏感列脱敏，并限制行数、单元格、单结果和单轮累计字节。
- 本阶段证据仅来自注入式假模型、假执行器和内存字节流；未连接真实内网模型或数据库，未完成 Qt 界面和视觉验收。

## 7. DC-02 实施回执（2026-09-16）

本阶段只增加数据中心 Agent 的执行底座，没有改动旧手动 SQL、旧内网模型函数、主窗口或现有 UI 路由。源码仍位于无 QWidget 的 `tools/data_center/`，运行时依赖由宿主显式注入；导入这些模块不会连接数据库、访问模型网关或加载 Qt。

### 7.1 已实现的执行链

当前纯 Python 已形成可测试的纵向链路；模型和数据库端仍由宿主注入假实现，真实适配器要在 DC-03 接入：

```text
模型 tool_call
  → DataCenterPolicy（闭集参数与目标校验）
  → DataCenterToolRegistry（宿主只读能力门禁）
  → AgentQueryTool（绑定当前 tab 的 Session）
  → QueryExecutor（SQLDecision、方言、SQL 一致性、worker、超时/取消）
  → 注入式 read-only driver / hook
  → 原始类型 QueryResult
  → ResultProjector（行、单元格、结果和本轮累计字节上限，敏感字段脱敏）
  → ToolResult role=tool / tool_call_id
  → AgentRunner 下一轮模型请求
```

`DataCenterToolRegistry.execute()` 是 `AgentRunner` 当前使用的入口。对于 `query_readonly`，注册表会在返回前调用 `ResultProjector`，所以真实 Agent 路径不会把 worker 的原始 `QueryResult` 直接放回模型上下文；`execute_for_model()` 仍保留给显式调用方。结果投影预算按 `(run_id, tab_id)` 维护，新 run 使用新的累计预算。默认模型投影最多 50 行，硬上限 100 行，单元格最多 512 字符，单结果最多 12 KiB UTF-8，本轮累计最多 48 KiB；明显凭据字段按列名脱敏，NULL、空串、数值、日期和 bytes 的类型信息保持区分。表格 UI 将来需要消费原始类型结果时，必须继续与模型投影分开。

### 7.2 SQL AST 门禁

`SQLPolicy` 使用 `sqlglot==30.18.0` 解析，并返回结构化 `SQLDecision`。DC-02 只允许单条 AST 根为 `SELECT` 的查询和无写入子树的只读 `WITH`；MySQL/OceanBase MySQL 使用 MySQL 方言，Oracle/OceanBase Oracle 使用 Oracle 方言，达梦当前走受限的 Oracle 兼容子集。多语句、解析失败、DML/DDL、事务和会话控制、锁、`SELECT INTO`、序列 `NEXTVAL`、MySQL 变量赋值、文件/外部访问、阻塞和已知副作用函数都会拒绝。Oracle `UTL_*` / `DBMS_*` 包命名空间整体拒绝，不能靠新增一个函数名绕过有限 deny-list。

解析结果不是安全证明。`QueryExecutor` 只接受与本次请求 SQL 和会话方言严格一致、且 `allowed is True` 的 `SQLDecision`；普通 bool、陈旧决策、SQL 或方言不一致都会在驱动前拒绝。没有结构化决策或没有显式只读驱动入口时返回 `READ_GUARD_NOT_READY`，不会把通用 `execute` 或 `cursor.execute` 当成只读能力。

### 7.3 会话、worker 与取消边界

`SessionManager` 只保存不可变的 `session_id / connection_id / generation / dialect` 和宿主工厂引用，不持有数据库连接。复用标签或连接时 generation 单调递增；`QueryExecutor` 在 `ThreadPoolExecutor` worker 中创建和关闭驱动，并为每次调用固定 `query_id、session_id、generation、deadline、row_limit`。默认查询时限为 15 秒，AgentQueryTool 将模型请求的行数限制在 1–100；旧 generation、关闭标签或目标连接不再接收结果和回调。

停止和超时是协作式的：宿主 token 会阻止新的模型轮次/工具，worker 在执行前后检查 token 和 deadline，排队中的 Future 可取消，晚到结果会被丢弃。同步调用会在 deadline 到达时先返回 `TIMEOUT`；若驱动忽略取消，原 worker 仍负责等待并关闭自己的 driver，且任务清理前不能复用同一 `query_id`。Python 层不能保证正在运行的数据库语句立即停止，也不能使用 `QThread.terminate`、强杀线程或关闭其他标签连接伪装成成功停止；此时应保留 worker 引用并向 UI 表示“正在等待停止/结果未知”。当前所有关系库 lease、Redis 和 Mongo facade 都不会宣称已具备驱动级取消；关系库 `supports_cancel=False`，只有未来形成 owner worker 内的真实取消通道并完成对应驱动验证后才能打开。

### 7.4 引擎能力矩阵与边界

|引擎模式|解析/操作入口|DC-02 当前状态|能力元数据|真实能力状态|
|---|---|---|---|---|
|Oracle|Oracle 方言，`query_readonly`|SQLDecision + worker 只读入口|只读事务；驱动取消未开放|未接真实驱动|
|MySQL|MySQL 方言，`query_readonly`|SQLDecision + worker 只读入口|只读事务；驱动取消未开放|未接真实驱动|
|OceanBase Oracle|Oracle 方言，`query_readonly`|独立模式标识，不与 MySQL 模式混用|只读事务；驱动取消未开放|未接真实驱动|
|OceanBase MySQL|MySQL 方言，`query_readonly`|独立模式标识，不与 Oracle 模式混用|只读事务；驱动取消未开放|未接真实驱动|
|达梦|受限 Oracle 兼容方言，`query_readonly`|明确标记受限兼容子集|只读事务；驱动取消未开放|未接真实驱动|
|Redis|结构化 `SCAN/TYPE/TTL/PTTL/GET/STRLEN/HGET/HSCAN/LLEN/LRANGE/SCARD/SSCAN/ZCARD/ZRANGE/ZSCAN/XLEN/XRANGE`|不接受 command 字符串；拒绝写、脚本、管理、订阅和阻塞操作|不提供事务/取消能力|未接真实单机/集群客户端|
|MongoDB|结构化 `find/count/aggregate`|递归拒绝 `$out/$merge/$where/$function/$accumulator`、未知表达式和未登记阶段；集合范围由宿主提供|不提供事务/取消能力|未接真实单机/副本集客户端|

关系库 lease 只使用宿主注入的 profile/secret loader 与 connector，在 worker 内建立专用连接；驱动邻接层再次执行同一 `SQLPolicy/SQLDecision` 校验，直接调用也不能绕过 AST 门禁，并且永不 commit。Redis/Mongo 适配器只接结构化白名单请求。能力矩阵可以供后续 UI/模型 schema 使用，但它不代表对应驱动已经具备只读、取消、TLS 或集群验收证据。

### 7.5 当前测试与证据

DC-02 的行为测试覆盖 SQL AST 拒绝、方言映射、只读决策一致性、worker 线程创建/关闭、generation 隔离、超时/取消/迟到回调、关系库/Redis/Mongo 能力矩阵、递归 NoSQL 门禁、AgentQueryTool 的 tab/连接绑定、注册表真实 AgentRunner 闭环、模型侧结果脱敏/截断/累计预算，以及包导入不加载 Qt/数据库驱动/旧模型配置。DC-01 的契约、运行器、流式解析和策略测试也已纳入联合验证。

当前联合验证命令为：

```text
.venv-build\Scripts\python.exe -m unittest discover -s tests -p "test_data_center*.py" -v
.venv-build\Scripts\python.exe -m unittest tests.test_intranet_llm tests.test_architecture_boundaries -v
```

截至本回执更新时，第一条命令的 147 项数据中心用例与第二条命令的 14 项兼容/架构用例全部通过，共 161 项，退出状态均为 0。范围包含 DC-01/DC-02 的既有联合用例、本轮 DC-03 宿主端到端模拟、直接驱动边界攻击、模型 deadline/缓冲限制、NoSQL 取消/截止时间/增量边界以及旧内网模型兼容检查。所有这些用例都使用假模型、假 driver、内存 client 或导入 smoke test，不包含真实数据库、真实内网模型或 UI 运行证据。

当前证据全部来自内存字节流、假模型、假 driver 和导入 smoke test。尚无真实 Oracle、MySQL、OceanBase 两种模式、达梦、Redis 单机/集群、Mongo 单机/副本集或真实内网模型 tools 能力证据，不能据此宣称生产只读安全或模型兼容。

### 7.6 DC-03 当前工作区实施范围（部分实现）

本阶段新增组件均位于无 QWidget 的 `tools/data_center/`，与旧手动 SQL、旧内网模型函数、主窗口和既有 UI 路由隔离：

- 模型边界：`model_config.py` 按选定配置 ID 懒加载并生成不含端点/凭据的 `AgentModelSnapshot`；`host_model_adapter.py` 将快照绑定到流式模型适配器，按 `native_tools`、`strict_json`、`text_only` 或未知能力决定请求形状。单次模型调用使用一个总 deadline，SSE 心跳和 socket timeout 不续期；推理摘要、正文、工具参数和原始流都有硬上限。默认只接收 `reasoning_content` / `reasoning_summary` 及配置明确登记的字段，普通 `reasoning` 字符串不作为可展示思考。私有 transport 配置只留在宿主适配器内。
- 关系库边界：`readonly_driver_adapters.py` 提供 Oracle、MySQL、OceanBase 两种模式和达梦的只读策略及 DB-API 适配边界；`readonly_lease.py` 固定连接 ID、profile revision、provider、mode 与 dialect，在 worker 内创建/初始化/关闭 lease。驱动邻接层复用正式 SQL 策略，拒绝不完整 profile 和目标覆盖；真实取消通道未验证前明确返回不支持。
- Redis/Mongo 边界：`nosql_codec.py` 对文本、二进制、日期、数值和 BSON 类型做增量有界编码，并递归脱敏下划线及驼峰式敏感字段；`readonly_nosql_clients.py` 仅接受结构化 Redis 白名单或宿主授权集合的 Mongo `find/count/aggregate`，分页游标由宿主持有，拒绝脚本、写入和扩大范围的操作。所有 driver 边界返回后再次检查取消/deadline；结果无法确认时关闭 owner/cursor 并返回稳定的未知状态，不伪报成功。
- 公共入口：`tools.data_center` 只导出模型快照/能力、宿主模型适配器、只读 lease 及必要错误/能力类型、Redis/Mongo facade 和不透明游标编码器；未把底层 driver adapter、矩阵别名或 codec helper 提升为包根 API。

本轮定向验证覆盖上述组件和包根导入安全；全部证据来自内存 transport、假模型、假 driver、假 Redis/Mongo client 和导入 smoke test。当前没有真实模型或真实数据库联调，也没有 `ui/data_center`、`panels/data_center_panel.py`、主窗口菜单/Tab 接线或视觉验收。

### 7.7 DC-03 之后的待办

- 用现有连接配置和 secure_store 驱动 `ReadOnlyLeaseFactory`，逐个引擎验证只读会话、取消、超时、TLS、行/字节边界和 `query_id` 证据；缺能力的组合只提供草稿。
- 用真实选定内网模型验证 `AgentModelHostAdapter` 的 SSE/tool_calls/usage 字段和错误策略。思考区只能展示供应商明确返回的 `reasoning_content` / `reasoning_summary` 增量；模型没有此字段时展示“模型未提供思考摘要”，不从隐藏思维链或普通文本推断思考过程。
- 通过 Qt 队列把 `AgentEvent`、工具进度、查询证据、正文增量和可展示推理摘要接入晴空棱镜数据中心 UI；当前没有数据中心 Qt 面板、真实菜单/Tab/标题栏接线或视觉验收。
- 继续实现需求正文中的连接树、内部 Tab、手动事务、结果表格、编辑 ChangeSet、历史收藏、导入导出、独立 SSH 隧道、兼容路由、迁移和发布包；这些不属于 DC-02 已完成范围。

本回执不能替代最终交付回执。提交前必须由主代理复核实际分支/SHA、仅暂存本任务文件、运行联合测试和 `git diff --check`，并把真实/模拟/视觉证据分开记录。

### 7.8 模型配置保存链检查点（2026-09-16）

基线提交为 `9b1c363cdb30cbae88aa754f1afc71ba836cb796`。现有 `tools/intranet_llm.py` 的配置归一化、旧格式迁移和目录保存链已保留 `agent_capability`、`agent_reasoning_fields`、单次 deadline 及四类缓冲上限。旧配置缺少能力时固定为 `unknown`；模型列表探测不改变能力，旧 `chat_completions(...) -> str` 请求体和 token 加密路径保持原行为。

`panels/settings_panel.py` 已提供显式能力选择、可展示推理字段输入和上述限制设置。字段名按明确列表保存，不支持通配符或任意对象；默认不根据正文猜测思考过程。这里声明模型协议能力，不代表该模型或真实数据库已通过兼容验收；`strict_json` 当前仍不产生可执行工具调用。

本检查点定向运行配置、设置页、数据中心模型配置/宿主适配和模型→只读查询模拟链共 38 项，通过；另运行架构边界与包导入安全 6 项，通过。设置页测试使用离屏 Qt 与内存配置，验证表单往返及既有分类/尺寸行为；未读取或写入真实用户配置，未访问模型/数据库，未做实际窗口视觉验收。关系库及 NoSQL 的新连接宿主工厂另处于主代理复核阶段，不纳入本检查点的已交付范围。
