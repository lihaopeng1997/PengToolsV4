# 数据中心 Agent：Luna 开发与主代理复核交接

日期：2026-09-16。状态：DC-01 纯 Python Agent 核心已实现；DC-02 只读执行底座已实现并通过注入式假模型/假驱动验证。真实数据库、真实内网模型、Qt 工作台与发布包仍未验收。唯一需求正文是[数据中心重构 V1](DATA_CENTER_AGENT_REQUIREMENTS.md)。先读根 AGENTS 和项目 README；最新用户决定优先于历史 UI-only、AI 不执行和系统标题栏限制。

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

DC-03先在专门测试库和已选内网模型验证真实工具能力；生成/查询意图区分、结构歧义、只读拒绝和有限结果回传全部通过后接Qt事件界面。SSE UI至少检查发送即时反馈、真实增量、思考/正文分区和停止。

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

停止和超时是协作式的：宿主 token 会阻止新的模型轮次/工具，worker 在执行前后检查 token 和 deadline，排队中的 Future 可取消，晚到结果会被丢弃。同步调用会在 deadline 到达时先返回 `TIMEOUT`；若驱动忽略取消，原 worker 仍负责等待并关闭自己的 driver，且任务清理前不能复用同一 `query_id`。Python 层不能保证正在运行的数据库语句立即停止，也不能使用 `QThread.terminate`、强杀线程或关闭其他标签连接伪装成成功停止；此时应保留 worker 引用并向 UI 表示“正在等待停止/结果未知”。引擎能力表中的 `supports_cancel` 是适配能力元数据，不等于真实驱动已经通过取消验收；当前 Redis/Mongo 标记为不支持，关系库也尚未完成真实驱动验证。

### 7.4 引擎能力矩阵与边界

|引擎模式|解析/操作入口|DC-02 当前状态|能力元数据|真实能力状态|
|---|---|---|---|---|
|Oracle|Oracle 方言，`query_readonly`|SQLDecision + worker 只读入口|支持事务/取消（仅元数据）|未接真实驱动|
|MySQL|MySQL 方言，`query_readonly`|SQLDecision + worker 只读入口|支持事务/取消（仅元数据）|未接真实驱动|
|OceanBase Oracle|Oracle 方言，`query_readonly`|独立模式标识，不与 MySQL 模式混用|支持事务/取消（仅元数据）|未接真实驱动|
|OceanBase MySQL|MySQL 方言，`query_readonly`|独立模式标识，不与 Oracle 模式混用|支持事务/取消（仅元数据）|未接真实驱动|
|达梦|受限 Oracle 兼容方言，`query_readonly`|明确标记受限兼容子集|支持事务/取消（仅元数据）|未接真实驱动|
|Redis|结构化 `SCAN/TYPE/TTL/PTTL/GET/STRLEN/HGET/HSCAN/LLEN/LRANGE/SCARD/SSCAN/ZCARD/ZRANGE/ZSCAN/XLEN/XRANGE`|不接受 command 字符串；拒绝写、脚本、管理、订阅和阻塞操作|不提供事务/取消能力|未接真实单机/集群客户端|
|MongoDB|结构化 `find/count/aggregate`|递归拒绝 `$out/$merge/$where/$function/$accumulator`、未知表达式和未登记阶段；集合范围由宿主提供|不提供事务/取消能力|未接真实单机/副本集客户端|

关系库适配器只调用宿主注入的只读 hook，不调用 `connect/commit/rollback` 或旧控制台执行函数；Redis/Mongo 适配器只接结构化白名单请求。能力矩阵可以供后续 UI/模型 schema 使用，但它不代表对应驱动已经具备只读、取消、TLS 或集群验收证据。

### 7.5 当前测试与证据

DC-02 的行为测试覆盖 SQL AST 拒绝、方言映射、只读决策一致性、worker 线程创建/关闭、generation 隔离、超时/取消/迟到回调、关系库/Redis/Mongo 能力矩阵、递归 NoSQL 门禁、AgentQueryTool 的 tab/连接绑定、注册表真实 AgentRunner 闭环、模型侧结果脱敏/截断/累计预算，以及包导入不加载 Qt/数据库驱动/旧模型配置。DC-01 的契约、运行器、流式解析和策略测试也已纳入联合验证。

当前联合验证命令为：

```text
.venv-build\Scripts\python.exe -m unittest tests.test_data_center_agent_contracts tests.test_data_center_agent_runner tests.test_data_center_streaming tests.test_data_center_agent_policy tests.test_data_center_sql_policy tests.test_data_center_session_manager tests.test_data_center_engine_adapters tests.test_data_center_readonly_integration tests.test_data_center_package_surface tests.test_architecture_boundaries -v
```

截至本回执生成时共 86 项通过，退出状态为 0。该数量包含假模型、假 driver 和包导入 smoke test；它不包含真实数据库、真实内网模型或 UI 运行证据。

当前证据全部来自内存字节流、假模型、假 driver 和导入 smoke test。尚无真实 Oracle、MySQL、OceanBase 两种模式、达梦、Redis 单机/集群、Mongo 单机/副本集或真实内网模型 tools 能力证据，不能据此宣称生产只读安全或模型兼容。

### 7.6 DC-03 之后的待办

- 把现有连接配置和 secure_store 适配为独立的 Agent 只读 lease，逐个引擎验证只读会话、取消、超时、TLS、行/字节边界和 query_id 证据；缺能力的组合只提供草稿。
- 把现有内网模型配置接入 `StreamingModelAdapter`，验证真实 SSE/tool_calls/usage 字段和错误策略。思考区只能展示供应商明确返回的 `reasoning_content` / `reasoning_summary` 增量；模型没有此字段时展示“模型未提供思考摘要”，不从隐藏思维链或普通文本推断思考过程。
- 通过 Qt 队列把 `AgentEvent`、工具进度、查询证据、正文增量和可展示推理摘要接入晴空棱镜数据中心 UI；当前没有数据中心 Qt 面板、真实菜单/Tab/标题栏接线或视觉验收。
- 继续实现需求正文中的连接树、内部 Tab、手动事务、结果表格、编辑 ChangeSet、历史收藏、导入导出、独立 SSH 隧道、兼容路由、迁移和发布包；这些不属于 DC-02 已完成范围。

本回执不能替代最终交付回执。提交前必须由主代理复核实际分支/SHA、仅暂存本任务文件、运行联合测试和 `git diff --check`，并把真实/模拟/视觉证据分开记录。
