# 任务：PengToolsHub「内网 Agent 工作台」改造 + 内置 Superpowers / 内网 skill 体系

你是资深全栈开发，请对仓库 PengToolsV4（Python 3.12 + PyQt6 离线桌面工具台，工作目录 `D:\development\workspace\WorkBuddy\PengTools`）实施以下改造，把「模型对话」升级为类 Codex / WorkBuddy 的**内网编程 Agent**。**全程不连互联网，严格按边界执行，改完必须自测。**

## ⚠️ 铁律（违反任何一条即返工）

1. **不连互联网**：运行代码不得引入任何 http/websocket 外网请求；模型只走现有 `tools/intranet_llm.py` 的内网 Base URL 配置，禁止新增联网依赖、在线 CDN、在线下载 skill。

2. **只允许改/新增以下范围**：
   - 改：`panels/model_chat_panel.py`（或新增 `panels/agent_workbench_panel.py` 作为工作台，二选一，见「一、架构选型」）。
   - 新增：`tools/agent_runtime.py`（受控工具运行时）、`tools/agent_store.py`（工作台会话存储）、`resources/ai_skills/manifest.json`（skill 清单）。
   - 改：`tools/ptools_harness.py`（仅把硬编码 `TASKS` 改为读清单，**不改 `run_task` 执行逻辑**）。
   - 新增内置 skill md 文件到 `resources/ai_skills/`。
   - 同步 `main_window.py` 导航 / Stack / 文案（若新增面板）。

3. **禁止改动**（核心执行与安全逻辑一律不动）：
   - `tools/intranet_llm.py`（模型调用、SSE、脱敏）
   - `tools/secure_store.py`（凭据加密）
   - `tools/sql_guard.py` / `tools/linux_guard.py` / `tools/ai_sql_draft.py`（三道安全门禁）
   - `tools/db_connect.py` / `tools/schema_snapshot.py` / `tools/tameng_agent.py`
   - `requirements.txt` 与 `scripts/build_release.ps1`（依赖不变，Agent 只用纯标准库 + 已含依赖）

4. **文件操作安全（最高优先级）**：
   - 每个<b>会话绑定一个工作文件夹</b>（会话创建/导入时选定，可后续更换），所有文件读写默认落在<b>该会话的工作文件夹内</b>；禁止越界读写 `data/`、`resources/`、系统目录、用户主目录（`C:\Users\...`）、`_MEIPASS`、以及其他会话的工作文件夹。
   - 路径必须 resolve（`os.path.realpath` + `os.path.commonpath`）校验，拒绝 `../`、绝对路径逃逸、符号链接逃逸。
   - 写文件 / 删文件 / SVN commit 必须**先展示 diff 预览 + 弹确认**，删除类默认焦点取消、危险标红。
   - **不提供任意 shell 执行工具**（无 `run_shell`），命令只能通过白名单工具 `run_test` / `run_svn`。

5. **数据落盘 `config.local_data_dir()`**：工作台会话、项目导入记录、用户 skill 全部写 `data/`（升级不丢）；禁止写 `resources/`（会打进 EXE 或被升级覆盖）。内置 skill 才放 `resources/ai_skills/`（只读）。

## 一、架构选型（先拍板再动手）

二选一，**默认推荐 A**（改动最小、复用现有模型对话面板）：

- **A. 扩展现有 `model_chat_panel.py`**：在现有面板内加「工作台 / 对话」两种模式切换（QStackedWidget 或 setVisible）。对话模式 = 现有逻辑不动；工作台模式 = 左侧项目树 + 中间对话 + 右侧文件/diff 预览。优点：不新增导航、复用会话存储。
- **B. 新增 `agent_workbench_panel.py`**：独立新面板 + 新导航项。优点：隔离干净；缺点：改动面大，需同步 main_window 导航/Stack/文案。

若选 A，请在回复里说明理由并保证「对话模式」零回归。

## 二、受控工具运行时（新增 `tools/agent_runtime.py`）

内网模型可能不支持原生 function calling，采用**「结构化输出 + 本地解析 + 受控工具」**协议：

1. 定义工具清单（第一版最小可用）：

| 工具 | 类型 | 作用 | 门禁 |
|---|---|---|---|
| `list_dir` | 只读 | 列目录（限定会话工作文件夹内） | 无 |
| `read_file` | 只读 | 读文件（限会话工作文件夹内 + 单文件 ≤ 200KB） | 无 |
| `search_code` | 只读 | 会话工作文件夹内关键字/正则搜索 | 无 |
| `write_file` | 写 | 创建/覆盖文件 | diff 预览 + 确认 |
| `edit_file` | 写 | old/new 精准替换 | diff 预览 + 确认 |
| `delete_file` | 危险 | 删除文件 | 二次确认 + 危险标红 |
| `run_test` | 半只读 | 跑项目 pytest 定向测试 | 命令确认 |
| `run_svn` | 危险 | SVN status/diff/commit（复用 `tools/svn_workspace.py`） | commit 二次确认 |

2. **工具协议**：模型被告知工具 schema；模型输出中若想调工具，输出约定格式（推荐纯 JSON 代码块：`{"tool": "read_file", "args": {...}}`）。本地解析后执行工具、把结果回填给模型、循环，直到模型输出最终自然语言答案。

3. **循环上限**：单次用户请求最多 10 轮工具调用（防死循环），超出则停止并提示用户。这个上限**包含 Plan 阶段的计划产出轮**——不能让「出计划」无限消耗轮次。

4. **复用已有 JSON 解析**：`tools/ptools_harness._extract_json_object` 或 `tools/ai_harness.strip_markdown_fence`，不要重写。

5. 工具结果需**脱敏**（复用 `sql_guard.redact_error`），不把敏感内容写进日志。

6. **执行范式：ReAct + Plan & Execute 组合**（新增要求）：
   - **Plan 阶段**：接到任务后，模型先输出结构化执行计划（步骤清单 + 每步工具/文件/预期结果），由 Superpowers 的 `writing-plans` skill 提示词驱动。计划以**可折叠步骤卡**展示在对话流中，每步执行完打勾/标状态。
   - **Execute 阶段**：按计划逐步执行，每步走 ReAct 循环（思考 → 输出工具调用 JSON → 本地执行 → 观察 → 下一步），直到完成或触及轮次上限。
   - **偏离即回规划**：执行中若发现新情况需改方案，允许回到 Plan 阶段重出一版计划。
   - **计划确认可跳过**：默认出计划后自动进入执行；提供 UI 开关「始终先确认」（默认关），开启后计划需人工确认/修改才执行。
   - **两套门禁独立**：计划的「确认开关」与写操作的「diff 预览 + 确认」是两套独立门禁，互不替代。即使开关关闭、计划自动执行，写/删/覆盖操作仍必须弹 diff 确认。

## 三、会话形式：工作台 vs 对话

- **对话**：沿用现有 `model_chat_store.py`，无工具、纯消息历史。零改动。
- **工作台**：新增 `tools/agent_store.py`，会话结构含：
  ```json
  {
    "id": "...", "type": "workspace", "title": "...",
    "workspace_dir": "D:/my/project",          // 会话绑定的工作文件夹（可更换）
    "messages": [...],                          // 复用 model_chat_store 的消息结构 + 额外字段
    "tool_calls": [...],                        // 每次工具调用记录（tool/args/result/时间）
    "created_at": "...", "updated_at": "..."
  }
  ```
  落盘 `data/agent/workspaces/&lt;id&gt;.json`，索引 `data/agent/index.json`（仿 `model_chat_store` 的原子写 + index 模式，直接借鉴其代码结构）。

## 四、工作文件夹（项目导入）

1. 工作台模式新增「导入/绑定工作文件夹」按钮 → 选本地目录（只读打开，不复制不移动）。**每个会话绑定一个文件夹做基础**，后续的读取、生成、改写、删除都发生在这个文件夹内。
2. 左侧显示该文件夹的文件树（轻量 `QTreeWidget`，懒加载：初始只列顶层 + 子目录，点开才展开；只索引白名单扩展名 `.py/.js/.ts/.vue/.html/.css/.scss/.md/.json/.txt/.yml/.yaml/.xml`）。
3. 工作文件夹路径记录落 `data/agent/workspaces.json`（升级不丢），下次可下拉选择已绑定过的文件夹；**会话运行中可更换工作文件夹**（更换后上下文里的旧路径快照随之刷新）。
4. 绑定/更换时**不读文件内容**（避免卡顿），只建目录结构缓存。

## 五、skill 清单化 + 内置 16 skill

### 5.1 清单驱动（改 `ptools_harness.py` 仅此一处）
把 `TASKS` 硬编码改为读清单（合并内置 + 用户两处）：
```python
# 内置默认：resources/ai_skills/manifest.json
# 用户覆盖：data/harness/skills.json
# 合并规则：用户清单优先，同名 task 覆盖内置；其余追加
```
**只改「TASKS 怎么来」这一处**，`run_task` 的执行逻辑、`_skill_for` 的兜底字符串、`load_skill_text` 的加载优先级一律不动。清单结构：
```json
{ "tasks": [ {"task": "sql.draft", "skill": "sql.md", "category": "后端开发"} ] }
```

### 5.2 内置 skill 清单（新增到 `resources/ai_skills/`，共 16 个）
- **已有 3 个**：`sql.md`、`sql_optimize.md`、`log_query.md`（保持原样）。
- **新增后端开发**：`mongo_query.md`、`redis_query.md`（配合数据中心 NoSQL 改造）、`code_review.md`。
- **新增前端开发**：`frontend_ui.md`（PyQt6 页面骨架四层 + QSS + 响应式规范）、`frontend_review.md`。
- **新增文档整理**：`doc_summary.md`、`doc_rewrite.md`。
- **新增需求**：`req_analysis.md`、`req_to_plan.md`。
- **新增设计**：`design_ui.md`、`design_arch.md`。
- **Superpowers 系列**（翻译 + 适配，见 5.3）。

### 5.3 内置 Superpowers（MIT 协议，翻译 + 适配）
Superpowers 是开源编码 Agent 工作流 skill 集，共 15 个。内网内置需：
- **全部翻译成中文**；
- **git 相关改写**：`using-git-worktrees` / `finishing-a-development-branch` 的 git 语义改为 SVN（复用 `svn_workspace.py`）或纯目录；无 worktree 概念；
- **去掉联网依赖**（原文有「fetch 安装脚本」之类，全删）；
- **TDD 示例改 Python/PyQt**（原 TS/jest 示例改 pytest）；
- **保留核心方法论一字不改**：先澄清（brainstorming）→ 再计划（writing-plans）→ TDD → 评审（requesting-code-review）→ 验证（verification-before-completion）。

对应 15 个 skill 的适配结论：
- ✅ 直接内置（翻译）：`brainstorming`、`writing-plans`、`executing-plans`、`test-driven-development`、`systematic-debugging`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`、`writing-skills`、`using-superpowers`。
- ⚠️ 简化合并：`dispatching-parallel-agents` + `subagent-driven-development` → 合并进 `executing-plans`（内网单机无并发子代理，改为顺序子任务 + 两阶段评审提示）。
- ⚠️ 简化：`finishing-a-development-branch` → 改为「提交/回滚决策」提示（适配 SVN）。
- ❌ 移除：`using-git-worktrees`（内网无 git worktree）。

Superpowers 的 skill 文件格式为 frontmatter（`name` + `description`）+ 正文工作流指令，与现有 skill md 一致，直接沿用。

### 5.4 skill 管理 UI
在模型对话面板（或工作台）加「skill 管理」入口：列出全部 skill（内置/用户，分类展示）、启用/禁用开关、新增（选 md 文件）、删除（仅用户级）。禁用 = 清单里标记 `enabled: false`，`run_task` 不识别该 task。UI 不新起独立导航，用对话框或面板内设置区。

## 六、完成后的自测（必须做，并在回复里汇报）

1. 语法检查：`python -m py_compile tools/agent_runtime.py tools/agent_store.py tools/ptools_harness.py panels/model_chat_panel.py`（用项目 venv：`C:\Users\Lenovo\.workbuddy\binaries\python\envs\pengtools\Scripts\python.exe`）。
2. 逻辑自查：
   - 对话模式零回归（现有聊天功能完好）；
   - 工作台模式能绑定工作文件夹、列目录、读文件、搜代码；
   - 写文件弹 diff + 确认，确认后才写盘；
   - 越界路径（`../`、绝对路径逃逸到工作文件夹外、符号链接）被拒绝；
   - 工具循环 ≤10 轮，超出停止；
   - ReAct + Plan & Execute 生效：任务先出计划（可折叠步骤卡），默认自动执行；开「始终先确认」后计划需确认；写操作 diff 门禁不因开关关闭而失效；
   - skill 清单驱动生效，禁用某 skill 后 `run_task` 报未知任务；
   - 数据全落 `data/`，无写入 `resources/`。
3. 若环境允许，跑定向测试：`tests/test_sql_guard.py`、`tests/test_page_skeleton.py`（不强制全量）。
4. 代码自查：确认未改 `intranet_llm` / `secure_store` / `sql_guard` / `linux_guard` / `ai_sql_draft` / `db_connect` / `schema_snapshot` / `tameng_agent` / `requirements.txt` / `build_release.ps1`。

## 交付要求

- 严格按上述范围改动，最小化，不重构无关代码。按「架构选型 → 工具运行时 → 会话形式 → 项目导入 → skill 清单化」顺序落地。
- 回复列出：选型（A/B + 理由）、改了/新增了哪些文件（精确到函数）、自测结果、风险点与不确定项。
- 若源码行号与你看到的不符，以函数名和字符串内容为准。

## 重要提醒（顺序依赖）

本需求依赖前几轮成果，请先确认以下已就绪（若未就绪，只做不依赖的部分）：
- 第四轮「模型对话接入 Harness」完成后的面板结构；
- 第三轮「数据中心」修好的 schema 快照；
- 第五轮「skill 配置化」的清单驱动基础（本需求的 5.1 与之合并，不要重复实现两套清单）。

若前几轮未完成，本需求**先只做「内置 Superpowers + 内网 skill 翻译适配 + 清单文件」**这一可独立交付的部分，Agent 工作台（工具运行时 + 项目导入）等前几轮落地后再做。
