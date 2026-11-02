# 任务：PengToolsHub「Harness 技能（Skill）配置化」改造

你是资深全栈开发，请对仓库 PengToolsV4（Python 3.12 + PyQt6 离线桌面工具台，工作目录 `D:\development\workspace\WorkBuddy\PengTools`）执行以下三项改造。**严格按边界执行，禁止扩大改动范围，改完必须自测。**

## ⚠️ 铁律（违反任何一条即返工）

1. 只允许改三处：① 任务注册（`tools/ptools_harness.py` 去硬编码 + `tools/harness_project.py` 新增清单读取函数）；② 内置 skill（`resources/ai_skills/` 新增 2 个 md）；③ UI（`panels/model_chat_panel.py` 加「skill 管理」入口 + 对话框）。

2. **禁止改动**：`harness_project.load_skill_text()` 的三层加载优先级（用户 → 项目 → 内置 → 兜底）、安全门禁（`ai_sql_draft` / `sql_guard` / `linux_guard` / `tameng_agent`）、连接与查询执行（`db_connect.open_connection` / `_run_redis` / `_run_mongo` / `run_read_query`）、凭据加密（`secure_store`）、打包（`requirements.txt` / `scripts/build_release.ps1`）。

3. **不新起导航/面板/Stack 页**。skill 管理用对话框（QDialog）实现，不是独立页面。

4. 用户 skill 与清单只能落 `data/harness/`（`config.local_data_dir()`），**禁止写进 `resources/`**（会被打进 EXE 或升级覆盖）。

5. skill 仍是「提示词文本」，不是可执行代码。新增 skill **不得绕过** `sql_guard` / `linux_guard` / `ai_sql_draft` 三道安全门禁。

## 一、任务注册从硬编码改为「配置清单驱动」

**根因**：`tools/ptools_harness.py` 的 `TASKS` 字典（约 18-22 行）把「任务名 → skill 文件」写死，`run_task()` 遇到未注册任务抛「未知任务」。用户新增 md 不会被识别。

**改法**：

1. 新增清单文件 `data/harness/skills.json`（用户级，落 `local_data_dir()`），结构：
```json
{
  "tasks": [
    {"task": "sql.draft",    "file": "sql.md",         "title": "生成 SQL 草案",  "desc": "自然语言转 SQL", "enabled": true, "builtin": true},
    {"task": "sql.optimize", "file": "sql_optimize.md","title": "优化 SQL",       "desc": "优化已有 SQL", "enabled": true, "builtin": true},
    {"task": "linux.query",  "file": "log_query.md",   "title": "Linux 只读查询", "desc": "自然语言转只读命令", "enabled": true, "builtin": true},
    {"task": "mongo.query",  "file": "mongo_query.md", "title": "Mongo 查询",     "desc": "自然语言查 Mongo", "enabled": true, "builtin": true},
    {"task": "redis.query",  "file": "redis_query.md", "title": "Redis 查询",     "desc": "自然语言查 Redis", "enabled": true, "builtin": true}
  ]
}
```

2. 在 `tools/harness_project.py` 新增两个函数（与现有 skill 加载同域，不要改 `load_skill_text`）：
   - `list_tasks()`：返回「内置默认清单（代码常量 DEFAULT_TASKS）+ `skills.json` 用户覆盖」合并后的任务列表，同名 task 用户覆盖内置；`skills.json` 缺失或解析失败时返回内置默认。
   - `resolve_task_file(task)`：返回该 task 对应的 skill 文件名；未知返回 `None`。

3. 改 `tools/ptools_harness.py` 的 `run_task()`：用 `resolve_task_file()` 替代硬编码 `TASKS` 查表（`_skill_for` 里的 `TASKS.get(task)` 改为 `resolve_task_file(task) or 'sql.md'`）。未知任务（`resolve_task_file` 返回 None）仍抛「未知任务」，来源变为清单而非代码常量。

**注意**：`TASKS` 字典里的 fallback 字符串（`_SQL_FALLBACK` / `_OPTIMIZE_FALLBACK` / `_LINUX_FALLBACK`）逻辑保留，只把「task → 文件名」的映射来源从硬编码改为清单。`sql.*` 返回 str、`linux.query` 返回 dict 的分支逻辑不变。

## 二、内置 skill 扩到 SQL + Linux + NoSQL（3 → 5）

保留现有 `sql.md` / `sql_optimize.md` / `log_query.md`，在 `resources/ai_skills/` 新增 2 个：

1. **`mongo_query.md`**：system 提示词，把自然语言转成 Mongo 只读查询草案（find 采样，禁止 aggregate/mapReduce/写操作，禁止回显完整文档）。只输出 JSON，参考现有 `sql.md` 的 JSON 输出风格。

2. **`redis_query.md`**：system 提示词，把自然语言转成 Redis 只读命令草案（GET/HGETALL/LRANGE/SMEMBERS/ZRANGE/SCAN/TYPE/TTL 等，禁止 SET/DEL/EXPIRE/FLUSHDB 等写命令）。只输出 JSON。

**注意**：这两个 skill 只负责「生成草案」，**不直接执行**。执行走第三轮已定的 `_run_mongo` / `_run_redis` 只读分支 + 写命令门禁，本次不动。

## 三、用户自定义 skill 的 UI 入口（放模型对话面板）

挂载点：`panels/model_chat_panel.py`，工具栏（`make_page_toolbar` 里 `model_combo` + `ping_btn` 附近）加一个 ghost 按钮「skill 管理」，点击弹出 QDialog。

**skill 管理对话框功能**：

1. **列表**：展示所有已注册 task（内置 + 用户），显示 title / desc / 来源（内置/用户）/ 启用状态。数据源 `list_tasks()`。
2. **新增**：选本地 `.md` 文件 → 填 task 名 + title + desc → 调用 `install_skill()`（已存在，把 md 复制到 `data/harness/skills/`）+ 写入 `skills.json` 注册条目。
3. **删除**：删除用户级 skill（内置不可删，仅可停用）。删 `skills.json` 条目，可选删除对应 md。
4. **启用/停用**：切换 `skills.json` 里对应 task 的 `enabled` 字段。
5. **编辑**：改 title / desc（skill 内容本身用文本编辑 md）。

**交互约束**：
- 删除操作「取消在左、确认删除在右，默认焦点取消」，符合项目删除类交互规范。
- 对话框遵守页面骨架风格，用 `ds-card` / `make_empty_state` 等既有控件（如适用）。
- 新增/编辑时校验：task 名非空且不重复、文件为 `.md`。

## 四、完成后的自测（必须做，并在回复里汇报结果）

1. 语法检查：`python -m py_compile tools/ptools_harness.py tools/harness_project.py panels/model_chat_panel.py`（用项目 venv：`C:\Users\Lenovo\.workbuddy\binaries\python\envs\pengtools\Scripts\python.exe`）。
2. 逻辑自查：
   - 内置 5 个 skill 全在清单，`run_task` 能识别；
   - 用户新增 md 注册后 `run_task` 可执行（无需改代码）；
   - 删除用户 skill 后内置不受影响；
   - 停用 skill 后调度跳过；
   - 三层加载优先级未变（用户 > 项目 > 内置 > 兜底）；
   - 安全门禁未被绕过；
   - 用户 skill 未写进 `resources/`；
   - 未新起导航/面板/Stack。
3. 若环境允许，跑定向测试：`tests/test_sql_guard.py`（不强制全量）。

## 交付要求

- 只改上述明确列出的文件与函数，改动最小化，不重构无关代码。三项改造按「一、二、三」顺序落地。
- 回复中列出：改了哪些文件、每个文件改了什么（精确到函数/行）、自测结果、以及任何你判断有风险或不确定需要我确认的点。
- 若某处源码行号与你看到的不符，以函数名和字符串内容为准，不要硬套行号。
