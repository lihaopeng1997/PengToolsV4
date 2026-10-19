# MuseBulid 集成只读审查（2026-09-30）

审查仓库：`https://github.com/lihaopeng1997/PengToolsV4.git`。隔离 worktree `C:\Users\Lenovo\.codex\worktrees\musebuild-review\PengTools` 为干净 detached HEAD `2019e867acceea5aa63026b6ebf5cd111d80bc43`（`origin/MuseBulid`），共同基线 `origin/main=32d7880af3d7246d0e586e3d7b2e16854e712cce`。主仓 `D:\PengTools` 在 `dev/2026-09-23`、HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`，有大量未提交 UI/数据中心改动；未写入主仓。主仓 HEAD 是共同基线后续提交，MuseBulid 本身并未“删除”尚未进入该基线的 Prism。隔离分支没有 `docs/project/README.md`，只读了其 `AGENTS.md`、`docs/README.md` 和主仓的项目入口以核对整合边界。

## 确认的问题

**P1（跨分支整合阻断；条件是整文件覆盖/仅移植导航模型）— 当前 Prism 壳会丢契约或把一级 AI 入口渲染为空标题菜单。** MuseBulid 的 `ui/navigation_model.py:34-66` 将 16/17 放入 `smartchat` / `aiworkbench` 单条目且空标题的组，`main_window.py:1442-1481` 按旧 Web 模型构造数据。当前主仓 `main_window.py:1894-1901` 调用 `build_web_nav_model_data`，`panels/dashboard_panel.py:6,690,1157` 调用 `get_dashboard_quick_tools`；这两个函数只在主仓 `ui/navigation_model.py:328-405`，MuseBulid 文件没有。仅替换导航文件会造成导入失败和首页失效；再以分支旧主窗口/首页整文件覆盖虽可避开这两个导入，却会回退当前 Prism shell。若只把 MuseBulid 新 NAV_MODEL 喂给主仓 Prism，主仓 `ui/prism_sidebar.py:89-119` 与 `frontend/src/chrome/nav.ts:199-233` 会将 16/17 变为标题 `''`、每组仅一个子项的弹出菜单，而非预期一级直接入口。无 GUI 投影断言已确认 `smartchat -> [16]`、`aiworkbench -> [17]` 且组标题均为空。**修复：**以主仓最新 Prism/标题栏/模块标签为底，把 16/17 直接入口、14/24 父级和工具箱二级的语义加入主仓模型生成器，并同步调整 Web/native Prism 投影；保留主仓 Web navModelChanged 与运行时解锁刷新。**验证：**主仓导入/投影单测，前端 typecheck、embedded 资源 build/verify（若修改）、无连接 offscreen 导航 smoke；视觉验收另做。

**P2（分支自身 Web 导航）— 空标题仍输出标点。** `ui/navigation_model.py:56-66` 的五个单条目组标题为空，但分支 `frontend/src/chrome/ChromeApp.vue:78-79` 无条件渲染 `{{ g.zh }} · {{ g.en }}`。默认 Web shell 启用时这些位置的文本实际为 ` · `。**修复：**空标题不渲染 label，或使用明确的分隔线元素；在当前 Prism 壳移植时沿其现有投影处理。**验证：**DOM/组件断言空组无标点，并做视觉验收。这里是静态模板推论，未运行 WebEngine/截图。

**P2（分支原生紧凑导航）— 工具箱主按钮在 icon-only 状态没有文字或图标。** `ui/navigation_model.py:53-54` 给 24 空 icon role，`main_window.py:868-871` 明确不设置 header 图标；`main_window.py:1145-1149` 在折叠/窄模式清空所有按钮文字，`ui/icons.py:245-249` 对空图标不设 QIcon，且 `ui/icons.py:94-119` 未登记 24。结果是工具箱 header 只有邻接展开箭头和悬停 tooltip 可识别，主按钮本身为空。**修复：**给 24 一个 Web/原生都实现的明确 icon role，或专门为无图标头保留可见识别符；验证原生 icon-only 模式。未进行桌面视觉点击。

**P2（发布清单）— rename 后 hidden-import 仍指向不存在的模块。** `scripts/build_release.ps1:351` 仍为 `panels.ai_workbench_panel`，实际模块为 `panels/sql_console_panel.py:237`，懒加载调用为 `main_window.py:1037-1038`。`importlib.util.find_spec` 验证旧路径为 None、新路径存在。**修复：**改为 `panels.sql_console_panel`，并在授权发布时做隔离包烟测。`main_window.py` 有静态 import，PyInstaller 可能自动收集新模块；没有执行打包，不能断言当前 EXE 一定缺面板。

## 已确认可保留的行为及整合注意

- 导航业务索引未搬迁：0–13 原含义和 stack 映射保留；16→stack13、17→stack14、18–23→stack15–20；14 和新增 24 为父级，15 旧模型父级移除。`main_window.py:515-532,1298-1306,1316-1339` 的 AgentWorkbenchPanel 确实挂载，未发现“只有菜单没有 Agent”的问题。无 GUI smoke 已验证索引和父级判定。
- SQL 面板 rename 的内容差异只有类名/docstring（Git 识别 99% rename），`sql_splitter_tab_id`、数据/会话存储路径没有被这项 rename 改动。活动源码/测试中的旧模块与类名引用已经更新；仅发布脚本残留。Dashboard `open_sql_console` → `MainWindow` → nav18 的信号路径齐全（`panels/dashboard_panel.py:240,395-406`，`main_window.py:303-304`）。
- `sidebar_expanded_toolbox` 虽未列入 `config.DEFAULT_SETTINGS`（旧 `sidebar_expanded_ai` 仍在 `config.py:94,245`），`normalize_settings` 保留未知键，`save_settings` 原样写回；纯函数 smoke 确认新折叠状态可持久化。旧“模型”组的折叠偏好不会迁到语义不同的工具箱，暂未归为数据损坏。`model_chat_last_session_id` 等键未受导航改动。
- MuseBulid `main_window.py:1970-1986` 解锁自我学习时只更新原生按钮；分支 `ui/web_shell.py:96-97` 无 navModelChanged，Web model 又是在启动时生成。这是共同基线已有问题，非本分支新回归；当前主仓 `main_window.py:2411-2427` 与 `ui/web_shell.py:99-102` 已修，整合须保留。
- 当前主仓的 `panels/ai_workbench_panel.py` 自共同基线已有大量 Prism/响应式变更，未提交修改还接入 `QueryStatusBand`。直接以分支 `panels/sql_console_panel.py` 覆盖会丢这批 UI 改动；应在主仓最新版面板上重放 rename 或保持兼容别名，随后更新调用、测试、发布入口。

## 建议的逻辑小提交/返工顺序

1. **SQL 命名事务（可用但需重放到主仓最新版）：**主仓当前 SQL 面板改名/类名、`main_window._ensure_db_panel`、Dashboard 信号/按钮、全部源码和定向测试引用、发布 hidden-import 同一提交；不要直接 cherry-pick `599bd87` 或末三条删除提交。
2. **导航语义事务（需改造）：**在主仓 `ui/navigation_model.py` 保留 `get_dashboard_quick_tools` / `build_web_nav_model_data` / `dashboard_label`，加入 14/24 父级、16/17 一级、toolbox children 与导航索引；同步主仓 `main_window.py`、`ui/prism_sidebar.py`、`frontend/src/chrome/nav.ts` / `ChromeApp.vue`、图标映射、设置刷新、测试与 embedded 产物。这里不能拆成“先 NAV_MODEL 后 UI 投影”的可用提交。
3. **功能事务（本审查未深入内部逻辑，勿借导航事务顺手合入）：**`panels/model_chat_panel.py` 与 `tools/ai_object_context.py` / `tools/ai_sql_draft.py` 及对应 intent/token 测试为一组；`tools/harness_project.py` / `tools/ptools_harness.py` / `tools/linux_guard.py` / `panels/ai_token_edit.py` 与 skill/guard 测试为另一组，应各自单独复核。MuseBulid 的 `599bd87..6779c0b` 是按文件批次同步、跨提交依赖，不能视为现成逻辑 cherry-pick 序列。

**可用：**保留导航 ID/stack 思路、Agent 挂载路径、SQL 命名意图与现有测试迁移。**需修改：**Prism 模型投影、空组/图标、发布 hidden-import、rename 对主仓最新版的重放。**不要整合：**整文件替换主仓 `main_window.py`、`ui/navigation_model.py`、`panels/dashboard_panel.py` 或 SQL 面板，以及依赖这些文件批次提交的直接 cherry-pick；会回退主仓已提交及未提交的 Prism/数据中心工作。

## 验证与边界

- `D:\PengTools\.venv-build\Scripts\python.exe -B -m unittest tests.test_main_window_self_refs tests.test_sql_guard -v`：21 项通过，只有既有测试打开文件未关闭的 ResourceWarning。
- 无 GUI/业务 I/O smoke：nav 顺序、14/24 父级、16–23 stack 映射、DB 槽位边界通过；旧/新面板 import spec 分别为 None/存在；新 toolbox 设置键及悬浮快捷过滤通过；Muse 模型输入主仓 Prism 投影后确认 16/17 空标题单子项组。
- `git diff --check origin/main...HEAD` 通过，隔离 worktree 仍干净。
- 未运行真实数据库/SSH/会话、桌面 UI 点击、WebEngine、前端 build/typecheck、打包/安装、视觉验收；本报告的 UI 问题分别标明静态模板推论或纯函数投影，不声称截图或用户视觉接受。
