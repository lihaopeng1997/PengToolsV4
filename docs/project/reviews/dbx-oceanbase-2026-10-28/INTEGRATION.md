# DBX 参考实现与 Oracle / OceanBase 接入方案

日期：2026-10-28。本文是源码与本地包核验及实施设计，不表示新驱动或新界面已接入。

## 核验范围

- PengTools：`main`，`998d1d76e3ff25bc431e3deeeac73a3f4f35e899`，工作区包含上一轮 Agent 修复，已保留。
- DBX：https://github.com/t8y2/dbx ，参考源码固定在 `3699fa053e9ab364454be63e2670f0f8329b451f`。主分支源码与用户的旧版本 JAR 分开核验，不能用最新源码依赖代替旧包事实。
- 用户本地文件：`C:\Users\Lenovo\Desktop\dbx-agent-oceanbase-oracle-0.1.53.jar`。
- 本轮未读取真实数据库配置、连接数据库、执行截图 SQL、修改用户 Java 环境或重打包软件。

## 本地包结论

| 项目 | 已核验值 |
| --- | --- |
| 文件大小 | 1,980,444 字节 |
| SHA256 | `d2080e9f6530b0627c06655925f63f76152f316729a1574bf4a5a2713c2e25fe` |
| 主类 | `com.dbx.agent.oceanbaseoracle.OceanBaseOracleAgent` |
| 主类 class major | 65，要求 Java 21 或兼容的更高版本 |
| 内含 JDBC | `com.oceanbase:oceanbase-client:2.4.18` |
| 其他已见依赖 | HikariCP 7.0.2、Gson 2.12.1、SLF4J 2.0.17 |
| 本机命令行默认 Java | 17.0.5，不满足该包主类要求 |

这是 OceanBase **Oracle 模式的驱动代理**，内部已经含 JDBC 驱动。不是 Windows ODBC 安装包，也不是可同时连接原生 Oracle 与 OceanBase 的通用驱动。不得填进现有 ODBC 驱动名称字段。

以上来自 ZIP manifest、嵌入的 Maven pom.properties、class 头和 javap 静态检查。文件名表示 0.1.53，manifest 未提供可独立验证的发行版本，尚未和官方发行物签名/摘要核对。当前参考源码依赖 OceanBase JDBC 2.4.19，与本地包的 2.4.18 有区别。

逐项归档检查：共 1,037 个目录条目，1,036 个可正常读取并通过 Python ZIP 读取校验；`META-INF/LICENSE.txt` 有重复条目，其中一次读取被 Python 报 `Overlapped entries`。因此不能宣称整个 JAR 完整性检查通过。这是归档结构异常，不能仅凭它认定驱动类损坏或不可运行；正式分发前应核对官方同版本文件，确保许可证条目和归档完整性。

## DBX 可以借鉴的部分

1. **驱动与界面分离。** 驱动是独立进程，通过标准输入/输出的逐行 JSON-RPC 2.0 通信；故障、取消与连接生命周期有独立边界。
2. **AI 有明确执行目标。** `AiAssistant.vue` 和 `aiComposerDatabases.ts` 区分对话绑定的连接、上下文数据库与执行数据库。不能让编辑器当前焦点悄悄改变已经提交的 AI 任务目标。
3. **按需加入对象。** 表由上下文引用加入，避免一开始把整个库的字段全部塞给模型。PengTools 可提供显式表选择，比仅靠 @ 操作更容易发现。
4. **会话与分页分别管理。** 连接会话的 `agentSessionId` 与查询分页的 `sessionId` 不是同一个标识。取消、取下一页、关闭游标必须指向正确对象。

截图的三个选择器不是“连接、数据库、表”，而是连接、数据库、Schema。本次查看的 OceanBase 实现中，`listDatabases()` 与 `listSchemas()` 都取 `querySchemas()`。本项目无需机械复制两个重复下拉框。

参考源码：

- [驱动说明](https://github.com/t8y2/dbx/blob/3699fa053e9ab364454be63e2670f0f8329b451f/agents/README.md)
- [OceanBase 实现](https://github.com/t8y2/dbx/blob/3699fa053e9ab364454be63e2670f0f8329b451f/agents/drivers/oceanbase-oracle/src/main/java/com/dbx/agent/oceanbaseoracle/OceanBaseOracleAgent.java)
- [多会话协议](https://github.com/t8y2/dbx/blob/3699fa053e9ab364454be63e2670f0f8329b451f/agents/common/src/main/java/com/dbx/agent/MultiSessionJsonRpcServer.java)
- [请求分发](https://github.com/t8y2/dbx/blob/3699fa053e9ab364454be63e2670f0f8329b451f/agents/common/src/main/java/com/dbx/agent/JsonRpcServer.java)
- [AI 面板](https://github.com/t8y2/dbx/blob/3699fa053e9ab364454be63e2670f0f8329b451f/apps/desktop/src/components/editor/AiAssistant.vue)
- [AI 数据库选择状态](https://github.com/t8y2/dbx/blob/3699fa053e9ab364454be63e2670f0f8329b451f/apps/desktop/src/lib/ai/aiComposerDatabases.ts)

## 最小接入结构

```text
原生 PyQt 数据中心：连接树 / SQL 编辑器 / 结果表 / 右侧 AI
                         ↓
现有 Agent 执行器：固定目标、模型调用、工具路由、审批、上下文压缩
                         ↓
统一数据库接口：列 Schema、查表、查字段、只读查询、受控写入、取消
             ┌───────────┴─────────────┐
        Oracle                     OceanBase Oracle 模式
   保留 python-oracledb             新增 DBX 代理适配器
                                          ↓
                               私有 Java 21 + 指定 JAR
                                          ↓
                                  OceanBase JDBC
```

本次新增集成范围仅 Oracle / OceanBase。不删除现有 Redis、MongoDB 等模块，不把 DBX 的整套桌面应用嵌入 PengTools。OceanBase MySQL 模式维持现有专用路径，不误用 Oracle 模式 JAR。

Java 21 应通过单独配置或随离线包携带的私有运行时启动，不修改系统 JAVA_HOME/PATH，不依赖内网自动下载。JAR、运行时路径、主类要求和协议能力先检查，再允许连接。分发时补齐所选 Java 发行版与驱动依赖的许可证、DBX Apache-2.0 文本及适用的 NOTICE。

### 必须处理的适配差异

- 现有 `tools/db_connect.py` 走 ODBC，`ui/connection_dialog.py` 保存 `oceanbase_oracle_provider=odbc`。需要增加显式新 provider 选项，保留旧配置，不能自动改写所有连接。
- 现有只读适配器采用 DB-API cursor 与绑定参数。对本地 JAR 的字节码检查表明，`execute_query` 消费 `sql/schema/maxRows/fetchSize/timeoutSecs`，该分支没有普通绑定参数入口。不能把带 `:1` 的旧查询直接透传，更不能自行字符串替换值。优先将元数据查询映射到 `list_tables/get_columns` 等 RPC；需要绑定值的功能应新增明确的参数化协议实现或暂不暴露。
- `execute_query` 的名字不表示只读。查询入口仍必须经过本项目 SQL 检查；分页、行数和超时限制必须显式传入。本地 JAR 默认 `maxRows=10000`、`timeoutSecs=0`，不能沿用为 AI 默认值。
- `SET TRANSACTION READ ONLY` 与后续查询必须在同一物理会话。不能把现有 ODBC 初始化顺序原样套到可能归还连接的 JDBC 池上；先验证事务固定会话能力，再开放 AI 查询。
- 取消由专用进程/会话通道处理。超时或进程退出后，将受影响会话置为不可继续使用；写入结果未知时不自动重试。
- 元数据接口、手工查询、AI 查询应共用目标身份与错误分类，避免出现“左侧能连，AI 连不上”的两套连接配置。

## 右侧 AI 的简化布局

```text
数据库助手                                      新对话
──────────────────────────────────────────────────────
可滚动对话
  用户：查询所选表最近的数据
  助手：正在读取字段 → 正在查询 → 结果摘要 / 可展开数据
  SQL 与执行详情折叠显示
──────────────────────────────────────────────────────
[连接 ▼] [用户 / Schema ▼] [选择表… ▼]
已选：SCHEMA.TABLE_A ×   SCHEMA.TABLE_B ×
输入问题…
[查询 / SQL草稿 ▼] [内网模型 ▼]                 [发送]
```

- Oracle 的服务/PDB 属于连接目标；Schema 是用户范围；物理表空间不在这里选择。
- Schema 单选，表可搜索、多选。默认选择当前用户可见的 Schema；未选表时在该 Schema 内搜索，已选表时服务端严格限制到这些对象。
- 切换连接后清空旧 Schema/表；切换 Schema 后清空不属于该 Schema 的表。执行中固定已提交目标，切换选择只作用于后续任务。
- 表范围必须进入不可变运行上下文、元数据过滤、SQL 对象检查、审批摘要及上下文隔离键，不能只写在提示词中。
- 输入框固定在底部；Enter 发送、Shift+Enter 换行，中文输入法候选确认不触发发送。用户正在阅读历史时保留位置，主动发送后定位到新消息，不跳顶部。

## 模型自己查询的执行流程

“查询”模式：获取已选目标 → 搜索/核验对象 → 读取必要字段 → 生成查询工具调用 → SQL 与范围检查 → 执行有限查询 → 根据结果回答。用户明确只要 SQL 时进入草稿流程。

目前源码已有 `query_readonly` 和相应系统提示词，但提示词本身不能保证模型完成工具调用。需要补齐终止条件：对于明确的数据查询任务，没有查询证据时不能标记为已查到；模型只输出 SQL 时，在有限轮次内提示补做工具调用，仍失败则显示具体失败原因。不从任意自然语言或 Markdown 中抽一段 SQL 静默执行。

内网模型使用原生工具调用时优先原生协议，不支持时走现有结构化文本协议。协议解析错误、模型等待超时、数据库错误分开显示与记录。工具结果先分页/截断，再送模型；原始结果保持可查看。

新增、修改、删除：必须展示目标、具体操作/SQL与影响范围，等待用户确认。确认绑定具体内容，模型改动内容后重新确认；“完全访问”不绕过这项最新规则。数据库 DDL 可能隐式提交，不能向用户承诺全部可回滚。确认后的写入与事务提交策略也必须明确显示。

长任务沿用既有 500 KiB 输入预算与提前压缩机制；500 KiB 是应用 UTF-8 字节预算，不等于模型的 token 上限。优先压缩旧工具结果与历史正文，保留当前目标、选中表、字段摘要、待办、最近失败和批准内容；整批工具调用/结果不得拆散。服务端另有限制时需取更小预算。

## 实施落点与验收

| 次序 | 实施内容 | 验收条件 |
| --- | --- | --- |
| 1 | `tools/data_center/` 新增独立进程协议适配；连接设置增加 JDBC 代理选择与运行时检查 | 实际 JAR 握手成功；缺 Java 21 时明确报错；密码不进命令行/日志；连接与关闭不遗留进程 |
| 2 | 元数据、查询及事务映射，与现有连接路由接通 | Oracle/OB 模式区分正确；Schema/表/字段正常；只读会话正确；分页、超时、取消有真实验证 |
| 3 | `ui/data_center/integration.py`、`agent_panel.py` 及父工作台增加统一目标栏 | 切换连接/Schema/表无旧目标串用；长名称/窄栏可用；回车发送滚动正常 |
| 4 | `RunContext`、元数据和 SQL 策略增加表范围；补齐查询完成条件 | 跨范围查询被拦截；仅输出 SQL 不被当成执行成功；结果可追溯到工具返回 |
| 5 | 受控写入与批准后执行路径 | 未确认不执行；改 SQL 后旧批准失效；结果未知不重试；拒绝后不执行 |
| 6 | 离线交付与诊断 | 私有 Java/JAR 随包可用；不访问外网；日志只含阶段、计数、耗时、错误码等允许字段 |

本轮已完成：官方源码关键链路阅读、本地 JAR 静态核验、现有项目差异定位与此接入设计。

未完成：Java 21 下启动握手、新 provider 实现与界面改造、真实 Oracle/OceanBase 联调、内网模型验收、新 EXE 打包。本轮不把历史 R3 包称为已经包含 DBX 集成。
