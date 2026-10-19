# DC03：六引擎工作台只读 Agent 装配

状态：本包实现与 TEMP 行为验证完成；整个数据中心工作台目标仍 active。当前分支 `dev/2026-09-23`，基准 HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。本地 dirty source 是依据并已保留；没有 stage、commit、push，也没有操作正在运行的 UI7。

## 实现范围

在生产 `AiWorkbenchPanel`、Redis 和 MongoDB 工作台的侧栏挂载现有 Data Center Agent Panel，并通过新增 `ui/data_center/integration.py` 复用既有 `DataCenterAgentController`、`AgentRunSnapshot`、`DataCenterAgentRuntime`、模型适配器、关系只读 host、NoSQL host 和 metadata host。没有改 Agent controller/panel、MainWindow 或编程工作台。

`tools/data_center/profile_host.py` 只读加载现有配置，保留配置 ID 与未知字段，不做 legacy normalization 或写回。模型列表异步加载；每次运行固定 model ID/revision 与 connection ID/revision/database/schema scope，worker 再次校验后才创建模型适配器、host/factory 和 runtime。凭据只在 driver boundary 使用；不会进入选择项、运行 context、事件或日志。目标/模型显式变更会取消活动 run；只切换 SQL Tab 只更新可见工作区目标，活动 controller、target label 和结果保持在原 run 快照。原 run 收尾后，下一次提交才固定当前 Tab。

SQL 放入动作将已完成结果的原目标（ID、revision、provider、mode、database、schema scope）与当前选择重新核对。目标不符时显示原因并保持编辑器不变；切回相同目标可放入。该动作只插入 SQL，不执行语句。关系工作台 subtitle 区分现有人工 AI 草稿与只读 Agent；原草稿功能和“生成不执行”路径保留。Agent 只读标记替换了旧模拟预览文案。三类工作台的 `agent_shutdown_cleanup()` 都只处理新 Agent 资源，非阻塞；manual worker/session 不在其关闭范围内。

## 引擎证据边界

- MySQL：生产 AiWorkbenchPanel → worker/controller → 固定 runtime → TEMP metadata → 只读 SELECT → evidence/final answer；fake connector、模型调用和 profile/model 读取均不在 UI 线程。手动连接和 `run_console_statement` 被测试替身设为失败路径；均未使用。
- MongoDB：TEMP fake client 上 metadata 与 `mongo_read` 共用同一 NoSQL factory、授权 collection ID、database 与 revision；仅访问 `orders`。
- Oracle、OceanBase Oracle 模式、OceanBase MySQL 模式、Redis：TEMP 配置下 controller/runtime 装配与模型文本结果边界测试；不是实际 driver lease/query 或引擎兼容证明。
- Dameng 是能力阻止：query controller/runtime 不安装，不发送模型请求、不创建 driver。不能表述为“六引擎均已兼容”。

这些测试不证明真实内网模型、真实引擎、SSH/TLS 或 unsupported topology 已联调。既有 host 的 fail-closed 仍有效。草稿 `text_only` 模式零 database/profile 读取、零连接，并保留旧 SQL 草稿入口。

## 定向验证

最终组合命令：

```powershell
python -m unittest tests.test_data_center_profile_host tests.test_data_center_workbench_integration tests.test_data_center_agent_controller tests.test_data_center_agent_panel tests.test_data_center_host_relational tests.test_data_center_host_nosql tests.test_data_center_metadata_runtime -v
```

退出码 0；88 项通过：新 profile/workbench 套件 14 项，既有 controller/panel/relational/NoSQL/metadata-runtime 回归 74 项。新增套件用例名：

- `test_default_readers_do_not_normalize_or_write_legacy_json`
- `test_exact_ids_unknown_fields_and_revision_drift_are_preserved`
- `test_agent_selection_controls_stack_in_a_narrow_sidebar`
- `test_all_three_production_workbenches_embed_the_agent_without_opening_manual_connections`
- `test_late_model_catalog_result_cannot_replace_the_users_new_selection`
- `test_model_and_connection_revision_changes_after_capture_do_not_run`
- `test_mongo_metadata_and_read_share_the_same_collection_allowlist_and_scope`
- `test_mysql_production_panel_runs_metadata_query_evidence_and_puts_sql`
- `test_shutdown_cleanup_is_nonblocking_and_clears_only_agent_threads`
- `test_sql_tab_switch_preserves_run_and_put_sql_checks_the_captured_target`
- `test_target_switch_cancels_old_snapshot_before_installing_new_target`
- `test_text_only_nonstream_draft_uses_zero_database_and_keeps_sql_draft_entry`
- `test_worker_assembles_each_supported_provider_mode_and_keeps_dameng_closed`
- `test_write_and_wrong_schema_queries_are_rejected_before_opening_a_lease`

另有 `python -m py_compile tools/data_center/profile_host.py ui/data_center/integration.py panels/ai_workbench_panel.py panels/db_redis_panel.py panels/db_mongodb_panel.py tests/test_data_center_profile_host.py tests/test_data_center_workbench_integration.py`，退出码 0；目标 tracked-file `git diff --check` 与新增 Python 文本尾随空白检查均退出码 0。

关键回归 `test_sql_tab_switch_preserves_run_and_put_sql_checks_the_captured_target` 通过真实 `sql_tabs.setCurrentIndex()` 信号链：model stream 阻塞时切换到 B 不取消 A；A run/evidence 完成且事件仍属于 A；B editor 拒绝 A 的 SQL；切回 A 后允许插入；关闭 owner 清理成功。没有执行 SQL。

Windows QPA TEMP 截图命令 `python C:\Users\Lenovo\AppData\Local\Temp\pengtools_render_dc_agent.py` 退出码 0，生成 960×980 中文和 1160×980 英文工作台截图。截图只使用 TEMP model/profile 读替身、Draft-only 模式；没有真实模型调用或 database connection：

- [中文 960px 工作台与 Agent 区域](data-center-agent-workbench-zh-960.png)
- [英文 1160px 工作台与 Agent 区域](data-center-agent-workbench-en-1160.png)

这两张图只证明 TEMP 组件已嵌入并可见，不构成视觉验收。英文工作台中 AgentPanel 的固定 caption/阶段/停止/提问文字仍有中文；草稿模式仍显示只读 badge；截图高度为 980px，未覆盖 960×640 目标视口下的完整 composer 操作区。窄屏/英语文案与模式 badge 的展示修正及用户视觉签收继续 pending，由后续工作区 UI 阶段处理；本包不扩大改动范围。

## 未完成

DC04–DC06 仍待做：统一数据中心导航/工作区、每标签手动事务与持久 session、typed results/编辑/ChangeSet、历史收藏、导入导出、配置迁移，以及 SSH/TLS 宿主与完整生命周期验收。主代理已安排后续 `manual_session` 生产宿主阶段；本包没有开始该工作。真实内网模型/数据库和用户视觉验收也仍 pending。
