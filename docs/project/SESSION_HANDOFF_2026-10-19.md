# PengToolsHub 详细交接 — 2026-10-19

本页是本次接手入口。先读本页，再按具体任务读取对应源码与回执；旧交接中的临时状态、禁止提交、待实现列表不得覆盖本页和当前源码。

## 1. 本次交付与 Git 状态

| 项目 | 实际情况 |
|---|---|
| 工作目录 | `D:/PengTools` |
| 当前分支 | `dev/2026-09-23` |
| 本次源码检查点 | **`b5ee4aa66c15bd09418d7a24b4c33a3fe743dfba`** |
| 提交标题 | `feat: integrate Prism UI and Agent data center workbenches` |
| 源码检查点规模 | 397 个文件，108896 行新增、6309 行删除；包含长期积累的源码、测试、嵌入资源和有效文档，不是本次新增这么多功能 |
| 提交前基线 | `818fba72bf437de9bd49fcd5b198fb1270096a53` |
| 远端 | `origin` → `https://github.com/lihaopeng1997/PengToolsV4.git` |
| 已 fetch 的相关远端 | `origin/ui/prism-v1` 仍为上述基线，没有需要合并的变化 |
| 当前分支上游 | 未设置；查询时远端没有 `dev/2026-09-23` 同名分支 |
| 本页版本 | 随后的文档提交；最终完整 HEAD 通过 `git log -1` 确认 |
| 推送 | **未推送**。用户授权提交和清理，没有指定本次远端推送分支 |

2026-10-19 用户明确要求提交当前工作、写详细交接，并进一步要求梳理本地项目和 Git 文件、清理无用内容。该授权取代旧交接的“不得 stage/commit”限制。没有切分支、pull、merge、rebase、stash、reset、clean、强推或改写历史。

源码检查点完成时 `git status --porcelain=v1` 输出为空。此后只整理本页和交接索引/验证记录；最终工作区状态由交付时的 Git 检查确认。有效源码已入 Git；用户数据、开发依赖、软件包和工具会话仍按既有规则忽略。

## 2. 目标、已确认决定和工作约束

长期目标仍是：重构底层 Agent 并集成软件；数据中心实现完整日常工作台；Agent 工作台能读取项目文件、生成修改预览、明确批准后应用修改。**整个产品目标尚未完成**，不能把检查点提交理解为最终验收。

- UI 使用单一 Prism `calm` 主题。Vue 负责标题/导航等轻量全局呈现，PyQt 保留数据库、SQL、SSH、文件交互等密集工作台。
- 数据中心统一入口；手动关系库默认手动事务。数据中心 Agent 只可自主查询，不能执行写入或调用编程 Agent 的文件/命令工具。
- 用户要求根代理亲自处理后续工作，不继续委派。**达梦暂不处理**，其方案文档仅供后续使用。
- 只对改动及受影响链路做最小有意义验证；不要每改一点就全量测试、重复打包。
- 真实数据、配置、密钥和已有会话原样保留；不得把实际端点、密码或生产内容写入文档、测试、日志或 Git。
- 用户此前通过物理 Esc 停止电脑操作，当前仍未恢复 GUI 操作授权。本次没有启动、关闭、切换或控制用户软件。需要实际用户窗口验收时先取得明确恢复授权；隔离 offscreen Qt 用例与用户窗口不同。

## 3. 技术栈与源码结构

当前是 **Python 3.12 + PyQt6/Qt WebEngine + Vue 3/TypeScript/Vite 的 Windows 混合桌面程序**。使用 QWebChannel 连接嵌入 Vue 和 Python；资源从本地运行，不新增 CDN 或公共服务。PyInstaller 输出 onedir 软件目录。

版本依据 `requirements.txt` 与 `frontend/package.json`：PyQt6 6.11.0，Qt/Qt WebEngine runtime 均 6.11.2；Vue 3.5.42、TypeScript 6.0.2、Vite 8.2.2；Redis SDK 8.1.0、PyMongo 4.17.0、Paramiko 5.0.0、SQLGlot 30.18.0。这些是依赖配置/本轮使用的 SDK 边界，**不代表内网服务器版本**。

```text
run.py                         应用启动/退出、单实例
main_window.py                 唯一软件壳装配、模块导航和跨页接线
panels/                        页级工作流、原生工作台
tools/agent_core/              通用 Agent 协议、循环、模型、上下文、预算
tools/agent_runtime.py         编程 Agent 工具与运行适配
tools/data_center/             无 QWidget 的数据库/工作区业务服务
ui/data_center/                Qt 控制器、结果模型、导入导出和对话框
ui/navigation_model.py        导航权威
ui/theme_manager.py           语义主题权威
ui/layout_metrics.py          响应式尺寸权威
frontend/src/                  Vue 源码：chrome、dashboard、shared bridge
resources/webui/vue/           同源生成的正式嵌入资源，必须一起提交
tests/                         按风险拆分的定向用例
scripts/                       构建和可复用开发诊断
docs/project/                  当前入口、需求、交接、精选回执
```

依赖方向为 `run → main_window → panels → tools / ui / config`。`tools` 不导入 `ui/panels`，`ui` 不导入 `panels`。不要把数据库对象跨线程传给 UI，或让前端直接访问文件/数据库。

## 4. 已接入的能力及证据边界

### 4.1 UI、首页与需求管理

已积累的改造包括：菜单平铺/展开、侧栏分组标题、首页辅助字号、重复页面上下文栏收紧、模块 Tab、关闭提示尺寸、常用工具跳转、设置保存隔离、需求目录扫描与工作线程生命周期、首页真实需求完成/测试点入口。

重点文件：

- `main_window.py`、`ui/navigation_model.py`、`ui/prism_sidebar.py`、`ui/prism_title_bar.py`、`ui/module_tabs.py`、`ui/confirm_dialog.py`。
- `frontend/src/chrome/ChromeApp.vue`、`nav.ts`、`frontend/src/dashboard/components/QuickTools.vue`、`MonthlyTasks.vue`、`StatsGrid.vue`。
- `panels/requirement_panel.py`、`panels/settings_panel.py`、`tools/svn_workspace.py`。

首页常用工具按权威导航 ID 对齐：数据中心 14、模型聊天 16、接口排查 12、日志排查 13、加解密 5、格式工具 11。旧数据库入口 18–23 保留内部兼容路由，不可因“旧入口”直接删掉。

首页示例数据有明确示例标记，不能修改成真实完成数据；真实需求需从需求管理导入/创建，并设置实际上线日期。扫描已有目录的路径、工作线程返回与台账刷新已做专项修复，最终新版完整窗口仍需用户验收。

相关追溯：`reviews/ui-fixes-2026-09-30/`、`reviews/ui-user-regressions-2026-09-30/`、`reviews/ui-completeness-2026-09-30/`。这些记录描述各自日期/包的证据，不代表今天整套源码的最终视觉通过。

### 4.2 Agent 核心、编程工作台与上下文

核心入口为 `tools/agent_core/runner.py:AgentRunner`；`contracts.py`、`model_adapter.py`、`streaming.py`、`budget.py`、`request_budget.py`、`context.py`、`memory.py`、`memory_context.py` 负责协议、SSE、预算、上下文和持久化边界。编程工具位于 `tools/agent_runtime.py:ProgrammingToolRegistry` 和 `run_agent_loop`，会话存储在 `tools/agent_store.py`，UI 在 `panels/agent_workbench_panel.py`。

历史已有外网 DeepSeek 的真实“读取工作区文件 → 生成修改预览 → 明确批准 → 应用修改”闭环记录；用户已在软件中自行保存测试配置。本次没有再次调用模型或读取 Key。数据中心与编程 Agent 的工具注册表保持隔离。

上下文已有序列化请求预算、压缩摘要、规范化/hash/CAS/epoch 防覆盖、恢复/冲突提示和物理 idle 门控。供应商私有 reasoning 不直接持久化或回传为工具授权；展示正文/允许的摘要/实际工具进度。

内网只已知家族名 **qwen3.6**。尚不知道精确变体、服务端最大 token 上下文、tools/SSE/usage 能力。现有约 64k UTF-8 字节的保守请求预算是本地限制，不能等同于服务端 token 窗口；不得臆测 262144、自动开启 thinking 参数或擅自换供应商。

历史协议模块 `tools/data_center/agent.py`、`contracts.py`、`model_adapter.py`、`streaming.py` 等有兼容转发作用。它们看起来很短，但仍有导入调用方，保留。

### 4.3 关系库与统一数据中心

统一组合在 `panels/data_center_panel.py` 和 `panels/ai_workbench_panel.py`。核心分区：

| 能力 | 主要文件 |
|---|---|
| 连接目录、固定目标、版本校验 | `workspace_catalog.py`、`connection_store.py`、`profile_host.py`、`object_tree*.py`、`host_metadata.py` |
| 手动会话、事务、执行所有权 | `manual_session.py`、`manual_driver.py`、`manual_relational_providers.py`、`host_runtime.py`、`ui/data_center/manual_query.py` |
| Agent 只读策略/lease | `sql_policy.py`、`query_executor.py`、`readonly_lease.py`、`readonly_relational_connectors.py`、`tool_registry.py` |
| 表查询、编辑、ChangeSet | `table_source.py`、`changeset.py`、`changeset_dialects.py`、`ui/data_center/table_editor.py` |
| 结果类型、查找、长值、导出 | `lob_values.py`、`manual_result_export.py`、`result_export.py`、`ui/data_center/result_model.py`、`result_find.py`、`value_inspector.py` |
| SQL 选区/脚本/补全 | `sql_script.py`、`sql_completion.py`、`completion_cache.py`、`ui/sql_editor.py` |
| 导入 | `import_preview.py`、`import_session.py`、`ui/data_center/import_controller.py`、`import_dialog.py` |
| SSH 与传输 | `ssh_tunnel.py`、`connection_transport.py`、`nosql_transport.py` |

每个手动标签独占会话/事务；Agent、元数据、导入分别有独立 owner。取消信号不是物理关闭，须等线程/驱动清理完成再释放门控；迟到结果不能写到新连接或新标签。写操作不自动重放，断线后的 UNKNOWN 不能伪装为成功。

结果保留 NULL、空串、Decimal、日期和二进制；LOB 在原 owner 线程转换。已有结果列隐藏/冻结、查找/详情、50/200/500 分页、服务端表筛选排序、逐语句结果等接线。SQL 可执行选区/当前语句/执行全部，支持完整选中的非结果集过程块；WITH 本地函数返回查询、输出绑定、DBMS_OUTPUT 展示仍有限制。

补全仅消费已加载、精确 connection/revision/db/schema 范围的目录/列缓存，不在键入时新增查库；处理别名、引用标识符和 Qt UTF-16 偏移。真实数据库厂商语义、取消/权限/会话行为仍要按实际服务验收。

### 4.4 MongoDB

- 默认查询使用 `limit + 1`，不做全集合 `count_documents`；保留 BSON 原值，Extended JSON 显示；PyMongo projection 通过 `Collection.find` 参数传入。
- 翻页在匹配 worker 成功后更新 offset/快照。失败保留旧结果/时间并标旧数据，重试同一页，物理 owner 结束后解锁。
- `tools/data_center/mongo_document.py` + `ui/data_center/mongo_document_edit.py`：按排序行对应的原始 `_id` 重读完整文档，Canonical EJSON 保真、纯预览、明确确认后一次 `replace_one(..., upsert=False)`。不允许修改 `_id`；完整替换会删除遗漏字段，也可能覆盖并发修改，UI 明示，无自动重试/回滚承诺。
- `mongo_transfer.py` 两层：导入单文档/数组/JSONL，64 MiB/10000 文档限制，文件快照先预览再逐条追加；遇失败/取消停止，明确 inserted/duplicate/unknown/skipped。
- 导出区分选中、本页、整个筛选查询；前两项用原始快照，整查询固定 filter/projection/sort、从 0 流式读取，无 count，512 MiB 文件预算。临时文件/fsync/原子发布，失败保留目标旧文件。导出为实时视图，不承诺一致性数据库快照。

以上已有纯业务、实际离线 PyMongo SDK、隔离原生 panel/worker 证据；没有真实 MongoDB 服务证明。

### 4.5 Redis

- `redis_scan_page` 保留原始 Key bytes。展示文字相同的不同 Key 必须经 Base64 候选明确选择，禁止从展示文本反编码猜 Key。
- `tools/data_center/redis_value.py:RedisKeyContext/RedisValuePage` 固定 connection/revision/DB/rawKey/selection_id。默认浏览 String 64 KiB GETRANGE、List 200 索引、Hash/Set/ZSet 游标、Stream 边界+lookahead 分页。
- 上一页/下一页/刷新与旧页失败标记已接入。TTL 0 保留，未知总数不猜；空的非末尾游标页可继续。排队的最新选择等待旧 owner，迟到结果和变更完成不能影响其他草稿。
- 当前删除/重命名/TTL 使用 `_start_captured_key_mutation` 固定原始 Key 和目标。显式手动命令与旧通用 helper 保留原语义。
- `redis_export.py` 两层提供显式选中 Key 的 JSON/Base64 查看性导出，六种常见类型增量读取；512 MiB 输出、4 MiB 单项、64 KiB Key 名上限，原子文件发布。它不是备份/恢复/导入。
- 默认浏览收取上限为 1000 项/8 MiB 页、4 MiB 单项，超限拒绝而非静默丢弃游标尾项。集合项限制发生在 SDK 收到响应后，不能据此宣称服务端响应字节或峰值内存已经硬限制。

**String/Hash/List/Set/ZSet 受控值编辑仍未实现。** 当前浏览与导出完成不等于值编辑完成，下一项先做这个，Stream 保持明确只读。

### 4.6 历史、收藏、草稿和工作区恢复

`tools/data_center/workspace_store.py:WorkspaceStore/WorkspaceSnapshot` 复用本地 SQLite；`ui/data_center/workspace_snapshot.py:WorkspacePersistence` 接入工作区显式 Save/Restore。保存 SQL/Redis/Mongo 原始编辑文本、BSON 查询快照、标签顺序和活动目标；原子保存，保留无关草稿/历史/收藏。

恢复只创建休眠草稿，不自动连接、不执行查询、不恢复事务/ChangeSet。配置变更的目标锁定，缺失目标仍可预览复制，重复恢复保留当前未保存编辑。存储 worker 和分步 UI 恢复有物理生命周期门控。

这是**手动保存/恢复**：没有新增每键自动保存、崩溃/退出检测、自定义查询标题持久化、表编辑 ChangeSet 恢复；不能从“保存布局”推导出这些能力。

## 5. 本次验证与历史证据

本次新增清理只影响 9 个未用导入、3 个无生产调用的诊断脚本、文档/忽略规则和生成文件。未做全量回归或安装包重建。

| 本次验证 | 结果/范围 |
|---|---|
| 4 个 NoSQL 业务模块 | 20 项通过，exit 0，0.521 秒：Redis bounded pages/export、Mongo document edit/transfer |
| 清理受影响的原生链路 | 7 项通过，exit 0，0.634 秒：Agent 布局、实际工作区菜单保存恢复、5 项 Redis 原生浏览/失败/排队/目标/删除回调 |
| Python AST | 250 个待提交 Python 文件可解析；未以此替代运行测试 |
| Vue | `typecheck`、`build:embedded`、`verify:embedded` 均 exit 0，生成的资源名与已有嵌入产物一致 |
| Git | staged diff review、`git diff --cached --check` 通过；发现的 6 个新文档/脚本空白问题已修正 |
| 凭据检查 | 对待提交候选做路径/内容模式审查，40 处 password/URI 命中经脱敏检查为测试夹具或认证模式常量；高置信 Key/私钥/Git token 模式无命中。这不是万能敏感信息认证 |

Vite 有未来 native config loader 兼容性提醒，当前构建成功；本次未借此升级依赖或更改模块制式。7 项用例初次输出包装器因 Windows GBK 打印失败，修正为 UTF-8 后重新运行并取得 exit 0；没有为输出问题改产品逻辑。

本次持久记录：

- [验证摘要 JSON](handoffs/2026-10-19/verification.json)
- [7 项原生链路日志](handoffs/2026-10-19/targeted-ui-tests.txt)
- [清理清单 JSON](handoffs/2026-10-19/cleanup.json)
- [源码检查点完整文件清单](handoffs/2026-10-19/source-checkpoint.json)

历史精选证据目录：`reviews/agent-refactor-2026-10-01/evidence/dc-ssh-mysql-finish-20261014/`，`result.json` 中按键定位：

| 键 | 当时证据，非本次重跑 |
|---|---|
| `scoped_completion_oct15` | 12 项补全相关用例通过，后续 2 项兼容细化通过 |
| `workspace_snapshot_oct15` | 14 项保存恢复相关用例通过 |
| `mongo_paging_oct15` | 7 项查询分页/BSON/实际离线 SDK 相关用例通过 |
| `mongo_document_edit_oct16` | 12 项文档编辑相关用例通过 |
| `mongo_transfer_oct16` | 12 项导入导出相关用例通过 |
| `redis_selected_key_export_oct16` | 13 项 Key 导出相关用例通过 |
| `redis_bounded_value_browser_oct16` | 12 项浏览/回调相关用例通过，后续旧页/语言/排队细化用例通过 |

这些数字存在共享受影响用例，不应相加冒充独立全量覆盖。真实 SSH 的历史最小探针、合成 RESP/Paramiko 和性能探针亦不等于目标数据库/内网服务已验收。

### 已知已有失败

`tests.test_nosql_loading_feedback.NoSqlLoadingFeedbackProcessTest.test_real_panel_actions_follow_fake_worker_lifecycle_without_network` 的旧大探针在精确 dirty-before Redis 源码上也失败于 command dispatch（无效 profile/draft，只手动设置展示 Key）。原测试文件保持原字节，未把它改绿；新用例走真实扫描、草稿和 raw Key 路径。基线日志 `redis-value-legacy-loading-before.txt` 保留。

另有早期 native manual integration 的 Qt teardown `0xC0000409` 既有边界，详见旧回执。当前 27 项定向验证不代表这些历史问题已消失；不要为清掉一个全量红灯修改无关产品代码。

## 6. 预览包与源码差异

当前保留的新预览包：

`D:/PengTools/dist/ui-agent-20261015-r8/PengToolsHub/PengToolsHub.exe`

SHA256：`d17298669e1de33588499758e1a356e36f37de0aca5d8838a5990a23af358847`，本次已重新核对文件存在和哈希一致。运行必须保留整个 `PengToolsHub/` 和 `_internal/`。

R8 历史构建及静态核对通过，记录为 61 个必需模块、215 个冻结源码代码对象、216 个 Python/7 个 Web 源码哈希及 Qt/ODBC 资源等；参见 `package-r8.json`。**R8 尚未由助手启动验收，且不包含后续 SQL 补全、工作区手动恢复、Mongo 分页/编辑/传输、Redis 导出/分块浏览等最新源码增量。**

R7 以及包含 data 的旧运行目录保留。软件单实例跨包路径生效，之后切新版应先通过软件自身正常退出旧版，不强杀，不迁移/复制真实数据和配置。`resources/build_info.json` 的日期是原有构建元数据，不是本次源码检查点日期。

本次未重打完整包。应完成明确源码缺口后再集中构建一次，做目标环境与视觉验收；不用每次小改都重复打包。

## 7. 清理、归档与保留边界

详见 [清理记录](PROJECT_CLEANUP_2026-10-19.md)。累计删除约 10.62 GB 可再生成文件；2453 个历史诊断文件和 3576 个旧源码快照完成逐文件哈希归档后移出项目。

归档在 `D:/PengTools-archive/cleanup-2026-10-19/`：`local-diagnostics.zip`、`historical-build-sources.zip`、对应 manifest 和 `packages/`。本地归档不在 Git；新机器接手以已提交源码/回执为准，若确需旧截图/精确 before 字节，再单独交接归档。

部分历史 `build_new_*/source/dist/PengToolsHub/` 及 `build/prism-preview-20260915-064701/source/dist/PengToolsHub/` 含实际 data，保留原路径与运行包；已清掉它们周围的重复源码、Installer 和 build。`data`、真实配置、依赖环境、工具状态不按垃圾处理。进一步批量源码缓存清理被自动审批拒绝，仅返回 `blocked by policy`；残留缓存已被忽略。

Git 保留有用的需求、历史审查和测试。没有因为类/模块名看起来旧或没有直接 import 就删除兼容 facade、动态路由、Qt signal 处理器或打包输入，也没有改写历史来“消除旧文件”。

## 8. 明确未完成项与推荐顺序

1. **先实现 Redis 受控值编辑**，按 [REDIS_VALUE_EDIT_NEXT.md](reviews/agent-refactor-2026-10-01/REDIS_VALUE_EDIT_NEXT.md) 接续，不重复大范围 SDK 调查。String/Hash/List/Set/ZSet 的编辑和预览确认均要做到产品入口；Stream 保持只读。
2. 按 [原始需求剩余核对](reviews/agent-refactor-2026-10-01/REMAINING_REQUIREMENTS_2026-10-16.md) 逐项复核。原始需求 7.3/7.4 与完整日常工作台不能缩减为“能查询/能导出”。需区分未实现、源码实现待真实验收、已知限制。
3. 按获准的真实环境分别验 Oracle/MySQL/OceanBase 两模式/Redis/Mongo、SSH、取消/事务未知、连接切换和关闭 owner；达梦延后。
4. 确认内网 qwen3.6 精确变体、真实窗口与 tools/SSE/usage，验证查询证据闭环和上下文压缩。不要把 DeepSeek 通过等同于内网模型通过。
5. 明确缺口修完后一次打包，检查冻结模块/资源，再在获准恢复 GUI 后验收新软件：字体、菜单平铺、关闭提示、首页真实任务、需求已有目录导入、数据中心和 Agent 文件闭环。

Redis 编辑的关键实现边界：

- 用 `RedisKeyContext`、不可变原始值和 table UserRole 身份，禁止使用截断/显示文本作为写入数据。
- String 当前页仅 64 KiB，不能 SET 成完整值；先在独立 owner 内分块加载完整可编辑值并施加明确上限，超限转只读/导出。
- 预览冻结原始目标、selector、old/new，编辑输入后旧预览失效。应用前重新校验 profile/type/旧值，明确确认后一次写；无变化不写。
- `WATCH/MULTI/EXEC` 不重放。Redis 8.1 SDK 的 cluster transaction 策略在创建 pipeline 时复制 retry，须在任务专用 client 创建 pipeline **之前**设零重试。EXEC 后连接故障可能也表现为 WatchError，不能全当成确定未写；丢失确认时报告 unknown。
- String 保留 TTL 的 KEEPTTL 需实际服务支持，不静默回落为清 TTL 的 SET。真实服务版本未知，不能假定最新服务语法。
- UI 直到实际 owner 结束才解锁；取消不能撤销已生效写，任务完成不能清除别的草稿。

## 9. 接手命令和最小复核

先只读确认，不自动切分支或同步：

```powershell
Set-Location D:\PengTools
git status --short --branch
git log -3 --oneline
git rev-parse HEAD
```

通用资料依次读：`AGENTS.md` → 本页 → `docs/project/DATA_CENTER_AGENT_REQUIREMENTS.md` 相关章节 → 对应源码/测试。不要从头加载庞大的历史会话、全部 before 副本或所有截图。

本次 20 项业务验证命令：

```powershell
.\.venv-build\Scripts\python.exe -B -m unittest tests.test_redis_value_page tests.test_mongo_document_edit tests.test_mongo_transfer tests.test_redis_export -v
```

7 项原生链路的完整用例名记录在 `handoffs/2026-10-19/verification.json`，日志为同目录 `targeted-ui-tests.txt`。之后仅重跑实际改动涉及的用例，不必原样重复全部 27 项。

Vue 修改验证：

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build:embedded
npm --prefix frontend run verify:embedded
git diff --check
```

完整 Windows 包的权威入口是 `scripts/build_release.ps1`（根 `build_release.ps1` 转发），输出 `dist/PengToolsHub/PengToolsHub.exe` 和配套 `_internal/`。构建前按脚本要求确认应用锁与数据保护，不拿历史快照脚本作为第二套发布规范。

## 10. 可直接交给接手者的任务描述

> 在 D:/PengTools 的 dev/2026-09-23 上继续。先读 AGENTS.md 与 docs/project/SESSION_HANDOFF_2026-10-19.md，核对实际 SHA/status。代码检查点 b5ee4aa66c15bd09418d7a24b4c33a3fe743dfba 已提交；之后为交接文档提交，未推送。不要重做 UI、Mongo/Redis 已完成的浏览导出和上下文预算基础。下一项是按 REDIS_VALUE_EDIT_NEXT.md 实现 Redis String/Hash/List/Set/ZSet 的受控值编辑，固定 raw Key/target，预览后确认，禁止自动重放，真实未知结果如实报告。达梦延后，root 亲自处理，只测改动风险。保护真实配置、用户数据和现有会话；用户 GUI 操作曾 Esc 停止，需明确恢复授权再碰用户窗口。旧诊断/快照已归档到项目外，核心回执已入 Git。源码比 R8 预览包新，整个产品尚未最终验收。
