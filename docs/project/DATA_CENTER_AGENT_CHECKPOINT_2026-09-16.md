# 数据中心 Agent 开发检查点：2026-09-16

本文件用于长会话续接。它保留本轮决定、文件轨迹、验证边界与下一步；需求正文仍是 [DATA_CENTER_AGENT_REQUIREMENTS.md](DATA_CENTER_AGENT_REQUIREMENTS.md)，开发接口与回执见 [DATA_CENTER_AGENT_DEVELOPMENT.md](DATA_CENTER_AGENT_DEVELOPMENT.md)。接手先核对实际 Git 分支、HEAD、远端与工作树，不能把本文记录当实时状态。

## 任务与固定边界

- 用户要求 Luna 最高推理级别舰队实施，主代理负责需求、边界、代码审查和联合验证。
- 首要目标是选定内网模型驱动多轮工具闭环；Agent 只执行查询，手动编辑器业务独立。
- 可展示思考摘要与正文真实流式更新，只接受明确字段，不提取隐藏思维链；工具参数完整验证后才可执行。
- 单主题晴空棱镜 `calm`；本轮宿主任务不修改 Vue、导航、标题栏或现有数据库工作台逻辑。
- 不访问真实用户配置、模型或数据库进行自动测试，不改 `data/`、凭据、运行体验包或既有会话。

## 已发布提交

分支：`ui/prism-v1`。2026-09-16 已发布宿主工厂提交 `03fc20e9b84489761d1a9f8c3d51e382eebbca3a`。分支核对与网页端接手方式见 [Git 核对记录](GIT_WEB_HANDOFF_2026-09-16.md)；不要把历史 SHA 当成实时 HEAD。

|提交|交付行为|
|---|---|
|`048a923d83fdbe8045c85e4cb86b57200867b6da`|隔离 Agent 核心、闭集工具协议、流式解析、去重与预算|
|`f1fcdf80f0cec34dd44fc77b3d063d6338a79d51`|SQL AST 门禁、worker 会话/代际、只读执行与模型结果投影|
|`9b1c363cdb30cbae88aa754f1afc71ba836cb796`|模型宿主快照、只读 lease、Redis/Mongo facade 与对抗加固|
|`aa02912974af48ad9743f52eecc67c6159fa5bb3`|现有配置保存链与设置页保留显式 Agent 能力、推理字段和限制|

## 本轮文件轨迹

|文件|关键职责/状态|
|---|---|
|`D:/PengTools/tools/data_center/model_config.py`|`AgentModelSnapshot`，选定 ID 懒加载，公开快照不含地址/凭据|
|`D:/PengTools/tools/data_center/host_model_adapter.py`|`AgentModelHostAdapter`，模型目标与运行上下文绑定|
|`D:/PengTools/tools/data_center/model_adapter.py`|单次总 deadline、增量读取、取消关闭、原始流/显示/工具缓冲上限|
|`D:/PengTools/tools/data_center/streaming.py`|任意 UTF-8/SSE 分片、明确推理字段、完整工具参数解析|
|`D:/PengTools/tools/data_center/readonly_lease.py`|`ReadOnlyTarget` 固定 ID/revision/provider/mode/dialect，worker 内 lease 生命周期|
|`D:/PengTools/tools/data_center/readonly_driver_adapters.py`|驱动邻接层再次复用 `SQLPolicy/SQLDecision`，永不 commit，取消未开放|
|`D:/PengTools/tools/data_center/readonly_nosql_clients.py`|结构化只读 facade，调用后取消/deadline 复查，有界分页与结果未知状态|
|`D:/PengTools/tools/data_center/nosql_codec.py`|增量编码、不透明游标状态和递归敏感字段脱敏|
|`D:/PengTools/tools/intranet_llm.py`|Agent 元数据配置归一化/迁移/保存；旧聊天请求 API 保持原行为|
|`D:/PengTools/panels/settings_panel.py`|显式 Agent 能力、推理字段、deadline 与缓冲限制表单|
|`D:/PengTools/tools/data_center/host_relational.py`|已随 `03fc20e` 发布宿主工厂；已复核内容 revision 与默认连接器 fail closed|
|`D:/PengTools/tests/test_data_center_host_relational.py`|已随 `03fc20e` 发布，11 项内存假驱动行为测试通过|
|`D:/PengTools/tools/data_center/host_nosql.py`|宿主工厂/lease/router 已通过本轮复核，真实驱动待验收|
|`D:/PengTools/tests/test_data_center_host_nosql.py`|17 项假 client 定向测试通过|

对应数据中心测试均以 `tests/test_data_center_*.py` 命名；设置保存链回归位于 `tests/test_intranet_llm.py` 和 `tests/test_prism_settings_navigation.py`。`tools/data_center/__init__.py` 已移除本轮额外公共导出，新增宿主模块先从各自模块直接导入，避免兼容别名扩散。

## 已运行验证

- `9b1c363` 提交前：147 项数据中心用例 + 14 项内网模型兼容/架构用例 = 161 项通过；编译与 diff 空白检查通过。
- `aa02912` 提交前：38 项配置/设置页/模型宿主与模拟闭环用例 + 6 项架构/包导入用例通过；编译与 diff 检查通过。
- 关系库宿主复核：11 项定向用例通过，验证默认旧连接器不建连、profile 内容变更使旧目标失效、secret 前置拒绝与 worker 所有权。

上述均为内存模型/假驱动/假 client、离屏 Qt 或导入检查。没有真实模型、真实数据库、实际窗口视觉、发布包或物理多屏证据。

## 待完成与续接顺序

1. NoSQL 宿主复核已完成：内容 revision、timeout 参数、拓扑、关闭清理与公共面已核对。
2. 主代理已运行宿主相关测试、已有只读闭环和架构边界共 60 项，通过；本检查点随宿主工厂提交，提交 SHA 以 Git 为准。
3. 实现经过验证的专用关系库连接器；默认旧 `db_connect.open_connection` 未具备连接前 timeout 契约，当前返回 `CONNECTOR_UNAVAILABLE`，不可据此开放真实 Agent 查询。
4. 按固定目标组合元数据 `search_objects/describe_object`、关系库 `AgentQueryTool` 与 NoSQL 路由，形成实际宿主运行入口；真实模型能力测试仅用无数据库 I/O 的回声工具。
5. 在专门测试库验证各引擎只读保护、超时、TLS、证据和有限回传；缺环境保持待验收。
6. 接 Qt 队列事件面板并验证即时反馈、思考/正文分区、停止、迟到事件与 UI 响应；再推进完整工作台、手动事务、SSH、迁移及打包。

已发现的待对齐点：当前模型显示缓冲超限会失败并停止整轮；需求中“只截断对应显示区并标明截断”的 UI 行为仍须明确实现和验收，不把现有保护当最终展示验收。

## 无关工作树与提交纪律

本轮不暂存 `resources/build_info.json`、UI 差异报告、五份阶段报告、诊断预览脚本，以及并行产生的 `docs/project/README.md`、`DEVELOPMENT_AND_AI.md`、`AI_CONTEXT_WORKFLOW.md` 变更。提交前重新核对实际状态；按文件逐项暂存，仅正常推送 `ui/prism-v1`。此前 GitHub OpenSSL TLS 中断通过单次 `http.sslBackend=schannel` 正常推送恢复，未修改持久配置或关闭证书验证。

## 技能使用

- `pengtools-lean-test`：按风险选择定向行为测试、导入/编译检查；联合测试只在共享协议与安全边界加固后执行，模拟不替代真实/视觉验收。
- `karpathy-guidelines`：限制文件所有权、移除本轮新增的兼容别名，修复已证明的 revision/timeout 问题，不顺手改旧业务。
- `context-compression`：本检查点按任务、提交、文件、证据、下一步和排除项保留可续接状态。

当前没有 Vue 改动，未使用 `pengtools-vue-ui`；没有把大量原始日志另存或引入外部服务。
