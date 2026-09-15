# 数据中心 Agent：Luna 开发与主代理复核交接

日期：2026-09-15。状态：DC-01 纯 Python Agent 核心已实现并通过内存模拟验证；真实模型、数据库与界面接线仍待 DC-02/DC-03。唯一需求正文是[数据中心重构 V1](DATA_CENTER_AGENT_REQUIREMENTS.md)。先读根AGENTS和项目README；最新用户决定优先于历史UI-only、AI不执行和系统标题栏限制。

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

DC-01不得声称只读数据库已验证：SQL AST、厂商只读会话、超时/取消和真实驱动在DC-02/DC-03完成后才开放真实执行入口。

## 3. 后续接线顺序

DC-02实现只读executor与SessionManager，新增sqlglot依赖须先通过指定方言案例和包体检查；不要把SQLGlot解析通过当安全保证。现有编程Agent文件工具不注册，现有run_console_statement自动commit路径不用于Agent。

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
