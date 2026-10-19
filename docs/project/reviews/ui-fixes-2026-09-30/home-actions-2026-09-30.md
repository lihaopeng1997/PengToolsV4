# PengTools 首页月度任务操作入口进度（2026-09-30）

## 最新续验：完整保存刷新已通过

本报告下方的 offscreen WebEngine 失败属于早期证据。后续使用 Windows QPA、生产 dashboard bundle、真实 MainWindow/Bridge/需求对话框和测试点编辑器，在独立 TEMP 合成数据中完成了两条保存链路：每次保存经原信号自然推送一次 summary，返回首页不额外推送；原 QWebEnginePage 和文档保持不变，完成状态及测试点计数同步正确，其他需求不变。无外部请求、socket 尝试或 renderer termination。详见 [dashboard-live.md](../ui-completeness-2026-09-30/dashboard-live.md)。用户视觉签收及生产数据行为仍不由该 fixture 推断。

- 仓库：`D:\PengTools`，分支 `dev/2026-09-23`，HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。未切分支、提交、推送或操作用户软件/数据。工作树原有大量其他人的未提交改动；本任务仅增量编辑已约定 owner 文件。
- 来源：AGY `gemini-3.8-flash-high --effort high --mode accept-edits` 实际编辑 `MonthlyTasks.vue`、`DashboardApp.vue`、`shared/bridge.ts`、`ui/web_shell.py`、`main_window.py`；GPT-6 审核修正 completion 文案、demo 校验、语义选择器，添加隔离测试。AGY 没有改需求面板或生成测试。

## 源码行为

- `frontend/src/dashboard/components/MonthlyTasks.vue`：仅真实且有非空非 demo ID 的月度任务提供「查看」「编辑状态」「测试点」按钮。新按钮分别发送 `completion` / `test_points`，行 Enter/Space 仍仅触发一次查看；无 ID 和 demo 行不显示操作、无键盘角色。`DashboardApp.vue` 再次检查 demo/ID/section 后调用 Bridge。
- `frontend/src/shared/bridge.ts`、`ui/web_shell.py`：新增 `openRequirementSection(id, section)`，仅导航定位，无业务写入。Python Bridge 拦截空 ID、大小写不敏感的 `demo-` ID、非法 section。
- `main_window.py`：host 同样校验，调用 `panel.focus_requirement_section(id, section)` 成功后才 `_show_panel(10)`；未知目标不导航到其他需求，切页失败也返回失败。
- `tests/test_dashboard_requirement_actions.py`：Bridge/host 实际信号和回调 7 项，另验证合成有效实际发版日期下，首页 done 只随既有 status=`已完成` 改变、测试点进度随现有字段改变。这里没有声称已调用真实编辑器保存。
- `tests/test_web_dashboard_runtime.py`：增加真实 Vue/QWebChannel DOM 断言（两个入口精确目标、查看不重复、无 ID / demo 不操作）。该测试因运行环境崩溃未到断言。

## 已验证

- `npm --prefix frontend run typecheck`：exit 0。
- `QT_QPA_PLATFORM=offscreen; D:\PengTools\.venv-build\Scripts\python.exe -B -m unittest tests.test_dashboard_requirement_actions -v`：7 tests，exit 0。设置 `PYTHONDONTWRITEBYTECODE=1`；所有数据为合成内存对象。
- `git diff --check -- frontend/src/dashboard/components/MonthlyTasks.vue frontend/src/dashboard/DashboardApp.vue frontend/src/shared/bridge.ts ui/web_shell.py main_window.py tests/test_web_dashboard_runtime.py`：exit 0。
- 菜单 worker 在源码 READY 后统一执行 `npm --prefix frontend run build:embedded` 和 `npm --prefix frontend run verify:embedded`：均 exit 0，生成 chrome/dashboard 双入口与 3 个引用。
- 需求面板 worker 完成 helper 与现有状态对话框集成；其定向命令 `QT_QPA_PLATFORM=offscreen; .\.venv-build\Scripts\python.exe -m unittest tests.test_requirement_scan_folders tests.test_requirement_save_failures tests.test_requirement_focus_section tests.test_requirement_worker_lifecycle -v`：50 tests，exit 0。其独立真实 `RequirementDialog.exec()` 合成 fixture 输出 `REAL_DIALOG status_combo_focus=True cancel_save=0`，exit 0；日志/fixture 位于系统 TEMP。这是该 worker 的结果，我没有重复运行。
- `tests/test_web_dashboard_runtime.py` AST parse：OK；不等于运行时通过。

## 未验与依赖

- Python 3.12.3、Qt 6.11.0、PyQt 6.11.0、PyQt6-WebEngine 6.11.0，Windows `win32`。新增 WebEngine DOM 测试的 offscreen 运行在断言前发生 GPU `Context lost during MakeCurrent` / shared context 失败；`--disable-gpu` 重试进程 exit `-1073741819`，`QT_OPENGL=software` + `--use-gl=swiftshader` 重试 exit `-1073740791`。不判为产品断言失败或通过，不再更换 flags。未操作用户 PID/真实软件。
- 需求面板最终 `focus_requirement_section(...,'completion')` 会在 host 显示需求页后的下一事件循环打开已有 `RequirementDialog` 并聚焦 `status_combo`，取消零写；`test_points` 定位已有编辑器。helper 对未知/demo/非法目标返回 False。面板 worker 的定向测试通过，但我没有运行首页 DOM 点击到宿主再到 modal 的全链路。
- 尚无本地首页 WebEngine DOM 链路/真实编辑器保存后的首页自然刷新通过证据。独立构建校验、Python 边界测试和面板 fixture 不可冒充这两项。

## 复用项

- `validate_requirement_section_target` 同供 Python Bridge 与 host 使用；`data-action=view|completion|test_points` 为后续 DOM 验收提供稳定目标。
- 测试只用有效合成 `actual_release_date`，没有因为 `online_month` 自动写真实发版日期，也没有扩张 summary 统计口径。
