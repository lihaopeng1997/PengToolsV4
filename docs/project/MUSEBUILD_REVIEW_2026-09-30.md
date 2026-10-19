# MuseBulid 功能分支审查与返工交接

状态：审查完成，功能修改待开发方返工，尚未合入 main。日期：2026-09-30。

## 基准与职责

- 仓库：`https://github.com/lihaopeng1997/PengToolsV4.git`。
- 远端真实分支名：`MuseBulid`，审查 SHA：`2019e867acceea5aa63026b6ebf5cd111d80bc43`。
- main 基线：`32d7880af3d7246d0e586e3d7b2e16854e712cce`，MuseBulid 多 9 个提交。
- 已发布 UI 基线：`ui/prism-v1`，SHA：`818fba72bf437de9bd49fcd5b198fb1270096a53`；它比共同 main 基线多 75 个提交。
- 当前本地：`D:\PengTools`，`dev/2026-09-23`，HEAD 同上述 UI SHA，另有尚未提交的 UI、数据中心和历史文件。远端审查不能看到这些改动。
- 当前主任务负责 Prism UI 续修、审查和验收；MuseBulid 的功能代码经过审查后按逻辑单元整合。不能把功能分支旧主窗口整文件覆盖到当前 UI。
- 本轮已将个人统筹技能的 Luna 实施队替换为 `gpt-6-sol` / `max`；agy 优先，Web GPT 按可访问证据使用，Grok 继续暂停。

main 尚未包含当前 Prism，不应把 MuseBulid 没有 Prism 的事实误报为它新删除 UI。风险出现在三方整合时。既有未提交文件继续保留，本轮没有提交、推送或合并。

## 必须先修的功能问题

| 级别 | 证据 | 触发与后果 | 最小修复与验收 |
| --- | --- | --- | --- |
| P1 | `panels/model_chat_panel.py:1059`，`tools/ai_sql_draft.py:236` | 成功生成并校验 SQL，聊天只显示解释，未显示 SQL | 展示通过校验的 SQL 草稿及未执行状态；成功回复必须包含 SQL，执行函数调用为零 |
| P1 | `tools/ai_sql_draft.py:314`，原门禁 `tools/ptools_harness.py:83` | 停用 `sql.draft` 后，新聊天链路仍直接调用模型 | 调用模型前统一检查任务启用状态；停用后模型和工具调用为零 |
| P2 | `panels/model_chat_panel.py:1043` | 下拉选择模型 B，草稿仍用全局活动模型 A，气泡标注与真实目标不一致 | 将本次选中模型配置冻结后传给 worker；双模型及停用组合回归 |
| P2 | `panels/model_chat_panel.py:966`、`:1059`、`:1097` | A 会话发送后切到 B，完成/停止回调用当前会话和旧消息 ID，A 永久 pending | 冻结 run/session/message 身份，回调按原归属更新；仅当前视图匹配时渲染。覆盖成功、失败、停止和迟到结果 |
| P2 | `panels/model_chat_panel.py:580`、`:118`、`:1059` | 请求连接 A 后切到 B，A 结构证据出现在 B 选择状态下 | 结果携带连接、快照、版本与请求代际，明确来源并过滤过期回调；两套合成目标测试 |
| P2 | `tools/chat_intent.py:15`，新增入口 `panels/model_chat_panel.py:883` | “请列出你支持的功能”被单字“列”判成取数，普通聊天遭拒 | 使用明确词组和对象上下文；低置信度走普通聊天或澄清。普通句与真实取数句双向测试 |
| P2 | `tools/linux_guard.py:120`，`panels/model_chat_panel.py:890` | Bash/zsh/未闭合围栏和单行命令漏标；text 日志被误当命令拦截 | 区分日志与 shell 命令，补常见围栏和明确命令形式的正反例。此处只做建议提示/门禁，没有自动远端执行 |

这批问题以源码和无真实模型/数据库的函数级复现为依据。现有测试通过不代表上述新路径已覆盖。

## UI 与发布整合边界

1. **导航不能整文件移植。** MuseBulid 不含当前 UI 依赖的 `build_web_nav_model_data`、`get_dashboard_quick_tools` 等契约。只移植其 NAV_MODEL 又会把 16/17 投影成空标题的单项菜单。2026-09-30 用户进一步明确“菜单还是平铺把，别整合在一起了”：所有业务叶子直接可见，分隔标题无交互，设置留在底部；不采用聚合工具箱或弹出分组。保留 Prism 的模型生成器、Web/native 投影、解锁刷新和业务 ID。
2. **空分组与工具箱图标需补齐。** 分支 Web 模板对空标题输出 ` · `；原生 icon-only 工具箱 24 缺可见标识。这两项为模板/投影证据，仍需实际视觉验收。
3. **SQL 面板重命名应在最新 UI 上重放。** MuseBulid 的 `sql_console_panel.py` 来自旧基线。当前面板已有 Prism/响应式及未提交状态带，直接替换会丢掉这些变化。保留当前面板内容，再统一改类名、调用、Dashboard 信号与测试引用，或保留兼容别名。
4. **发布清单漏改。** `scripts/build_release.ps1:351` 仍引用已删除的 `panels.ai_workbench_panel`。改为新路径并做隔离包烟测。新模块存在静态 import，尚不能断言 PyInstaller 产物一定缺模块。
5. **提交不是可独立 cherry-pick 的功能包。** 这 9 个提交按文件批次同步，先新增面板，再改调用/导航/测试，最后删除旧路径。不能逐条挑选并假定每条都可运行。

## 可保留与合入顺序

- 可保留：对象 token 的快照/指纹重绑定、SQL 证据缺失/过期/未知对象时拒绝生成的链路、只生成不执行的边界、旧入口统一停用门禁、导航 ID/stack 映射和 SQL 命名整理意图。
- 先修聊天执行归属、停用和 SQL 显示，再单独交付证据/token 与测试的逻辑提交。
- 停用门禁与新聊天入口一起验，不能只验证旧三个入口。
- SQL 重命名、所有调用和发布清单作为一个完整事务，重放在最新 UI 上。
- 导航语义、Prism Web/native 投影、图标、相关测试与 Vue 正式资源作为一个完整事务。
- 最后在保留当前本地现场的隔离整合分支验证 UI 与功能共存，通过后再确认具体提交范围并合入 main。不得整体暂存当前工作树。

## 可直接转发给 MuseBulid 开发方

> 请先回报实际读取的仓库、分支及完整 SHA。修复基准是 MuseBulid `2019e867acceea5aa63026b6ebf5cd111d80bc43`，兼容的已发布 UI 基准是 ui/prism-v1 `818fba72bf437de9bd49fcd5b198fb1270096a53`。按本报告先修 SQL 未展示、停用绕过、选中模型与会话/连接归属、意图误判和命令建议误拦/漏标；补功能行为回归，使用假模型、合成快照和临时存储。保持 SQL 草稿零执行，不能用真实生产环境补证据。用户最新要求菜单平铺直达，不采用聚合工具箱或弹出分组；导航与面板 rename 必须保留最新 Prism 契约，不覆盖主窗口、导航、Dashboard 或 SQL 面板整文件。补发布 hidden-import。每个提交按可运行的逻辑功能单元组织，同时包含调用和测试；返回完整 SHA、修改文件、测试命令/退出码、未验项。当前 UI 的未提交内容不可从 Git 推断，由主代理在最终整合时处理。未通过复核前不要合入 main。

## 验证与限制

- GPT-6 聊天审查：`tests.test_model_chat_intent`、`tests.test_token_rebind`，22 项通过；新成功回复、停用、会话归属等问题另以函数级无外部 I/O 复现。
- 主代理：`tests.test_skill_disable_flag`、`tests.test_ptools_harness`，24 项通过，证明已覆盖的旧入口有效。
- GPT-6 集成审查：`tests.test_main_window_self_refs`、`tests.test_sql_guard`，21 项通过；另验证导航/设置投影和旧/新模块存在性。
- 审查目标 `git diff --check origin/main...HEAD` 通过，隔离 checkout 保持干净。
- agy 做了静态独立意见。其“直接执行漏洞”“FileNotFoundError”“完全无单测”等说法经源码复核撤销，没有纳入本报告结论。
- 未验：真实模型/数据库/SSH、流式/真实超时、分支自身打包与桌面视觉。旧 skill 测试在导入时可能读取用户技能清单，未输出或改写内容，不能称绝对零配置读取。

详细证据：[聊天审查](reviews/musebuild-2026-09-30/CHAT_REVIEW.md)、[集成审查](reviews/musebuild-2026-09-30/INTEGRATION_REVIEW.md)。
