# PengToolsHub V4.27 Private — UI 改造会话交接

> **读者：** 完全没有上下文的新会话 / 新开发者
> **更新时间：** 2026-07-30
> **当前阶段：** 全局 UI 第一阶段已实现并完成构建验收；核心排障链路（接口排查、SSH/终端、日志）及其余模块的页面级 UI 改造尚未实施。

---

## 1. 一句话说明

本轮在重设计 PengToolsHub（Windows 离线 PyQt6 运维工具台）的全模块界面。先完成了所有模块的交互/视觉设计与审阅文档，再落实了“全局 UI 基础设施”第一阶段：主题 Token、QSS 状态样式、紧凑/舒适密度、侧栏启动偏好、公共页面骨架以及设置页/主窗口接入。

**下一步不是重新设计，而是按已确认的设计文档开始逐模块落地，优先接口排查。**

## 2. 当前仓库与工作区状态

- 工作区：`D:\development\workspace\WorkBuddy\PengTools`
- 产品：`PengToolsHub V4.27 Private`，Windows 离线桌面应用，Python + PyQt6。
- 远端：`origin https://github.com/lihaopeng1997/PengToolsV4.git`，目标分支 `main`。
- 当前最新已提交 commit：`e0c0119488044ee665055804c636e9821e0e4d95`（2026-07-28，`chore: 更新终端会话改造构建信息`）。
- **重要：本轮 UI 第一阶段源代码、测试、设计文档和审阅 HTML 当前仍未 Git 提交，也未推送。不要误以为远端已有这些改动。**
- 当前工作区有既有/本轮未跟踪设计 HTML 与 `docs/plans/`；提交时只挑选需要的源码、测试、计划/交接文档，禁止提交 `data/`、安装包、`build/`、`dist/`、临时日志或截图。

## 3. 已完成：全模块设计收口

所有方案均已由用户确认，先前没有实施业务面板改造。

| 模块 | 设计结果 / 审阅文件 | 实施状态 |
|---|---|---|
| 全局设计系统 | `docs/方案/全局设计系统与改造总览.html` | 第一阶段基础设施已实施 |
| 工作台首页 | `工作台首页设计方案.html` | 未实施页面重构 |
| 接口排查 | `接口排查改造计划.html`、`docs/plans/2026-07-29-interface-debug-workbench-redesign.md` | 未实施，**下一优先级** |
| SSH / 终端 | `docs/方案/SSH终端设计方案.html` | 未实施页面重构 |
| 日志排查 | `日志排查设计方案.html` | 未实施页面重构 |
| 发布、配置与环境 | `发布与环境管理设计方案.html` | 未实施页面重构 |
| 工具中心与辅助模块 | `工具中心与辅助模块设计方案.html` | 未实施页面重构 |
| 全局 UI 第一阶段 | `全局UI第一阶段实施计划.html`、`docs/plans/2026-07-29-global-ui-foundation.md` | **已实施并验收** |

### 3.1 全局 UI 第一阶段已实施内容

已修改的代码范围（未提交）：

- `ui/theme_manager.py`
  - 四套主题 `calm / clear / warm / night` 补齐全局设计 Token：紧凑/舒适控件高度、列表行高、焦点环、信息/成功/警告/危险状态背景。
  - 新增 `missing_theme_tokens()`、`unresolved_qss_tokens()`。
  - `ThemeManager.render()` 在 QSS 留有 `__[A-Z0-9_]+__` 未解析占位符时抛 `RuntimeError`，防止样式静默失效。
- `resources/style.qss`
  - 增加 `uiDensity=compact/comfortable` 的尺寸规则、页面标题/上下文、焦点环、四级状态、危险按钮等全局规则。
  - **终端 `TERM_*` 色彩规则必须保持独立，绝不能绑回 `CODE_BG`。**
- `config.py`
  - 新增兼容设置：`ui_density`（`compact` / `comfortable`）、`sidebar_collapsed`。
  - 加固字符串布尔解析，`"false"` 必须解析为 `False`，不能用裸 `bool("false")`。
- `ui/design_system.py`
  - 已恢复并保留既有 `apply_button`、`apply_tree`、`apply_table`、`apply_surface` 等公共 API；在其上合并 `DensityMetrics`、`DENSITY_METRICS`、`density_metrics()`。
  - 该模块不能 import `panels` / `tools`，仅提供公共视觉能力。
- `ui/page_chrome.py`
  - 新增无业务耦合的 `PageChrome` 容器（标题、上下文、次级操作、主操作槽位）。
- `panels/settings_panel.py`
  - 外观设置增加“信息密度”“启动时收起侧栏”，仍走既有 `settings_changed` 保存链路。
- `main_window.py`
  - 应用密度属性和侧栏偏好；**侧栏折叠只在窗口初始加载时应用**。
  - 运行期用户手动展开侧栏后，保存主题/密度等设置不得把侧栏再次强制折叠。
  - 导航 → Stack 映射、面板激活/离开副作用未改。
- `panels/sql_panel.py`
  - 当前系统摘要补充“前往系统配置切换”的操作提示。
- `tests/test_theme_responsive.py`、`tests/test_ui.py`
  - 增加主题 Token、QSS、密度、PageChrome、导航映射、侧栏运行期行为回归；修正搜索防抖等待、私有解锁状态隔离及当前界面断言。

## 4. 已完成验证与最终构建

以下证据来自最后一次改动后的重新验证，均成功：

```bash
QT_QPA_PLATFORM=offscreen "C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_theme_responsive tests.test_core tests.test_ui tests.test_secure_store -v
```

- 结果：**126/126 测试通过**。

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" scripts/scan_release_secrets.py
```

- 结果：**PASS**；HIGH RISK 0、WARN 0。

离屏启动已通过：创建 `QApplication` 和 `MainWindow` 成功。

最终构建命令：

```powershell
$env:PATH = "C:\Users\Lenovo\.workbuddy\binaries\python\envs\pengtools\Scripts;" + $env:PATH
& "D:\development\workspace\WorkBuddy\PengTools\scripts\build_release.ps1"
```

最终构建成功，安全扫描集成通过。构建信息：

```json
{
  "version": "4.27",
  "edition": "Private",
  "build_date": "2026-07-29",
  "build_time": "2026-07-29 15:02:00"
}
```

最终产物均已存在且时间一致：

- `D:\development\workspace\WorkBuddy\PengTools\dist\PengToolsHub.exe`（约 63 MB）
- `D:\development\workspace\WorkBuddy\PengTools\Installer\PengToolsHub.exe`（约 63 MB）
- `D:\development\workspace\WorkBuddy\PengTools\PengToolsHub_Offline_Setup.zip`（约 63 MB）

> 打包曾因正在运行的 `PengToolsHub.exe` 锁定 `dist\PengToolsHub.exe` 而失败；关闭全部 PengToolsHub 实例后，最终重建已成功。

## 5. 当前卡点

**没有代码、测试、构建或依赖卡点。**

当前真正的状态是：

1. 全局 UI 第一阶段已经完成，但源码尚未提交 / 推送；新会话第一件事应先 `git status`，审查变更后提交并推送（除非用户明确要求先继续开发、后统一提交）。
2. 页面级业务模块改造尚未开始。已确认优先顺序为：
   - 接口排查（核心排障链路第一优先级）
   - SSH / 终端
   - 日志排查
   - 工作台首页、发布与环境、工具中心及辅助模块
3. 用户已明确授权：后续无需反复询问，按既定设计和计划直接继续；仅在存在无法安全判断的技术/安全阻塞时说明原因。

## 6. 下一步执行计划（新会话直接照做）

### 6.1 首先：保护当前已完成成果

1. 阅读：`AGENTS.md`、本交接文档、`docs/plans/2026-07-29-global-ui-foundation.md`。
2. `git status --short`，不要清理或覆盖现有未提交文件。
3. 复验最小范围：

```bash
QT_QPA_PLATFORM=offscreen "C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_theme_responsive tests.test_core tests.test_ui tests.test_secure_store -v
```

4. 确认无误后，按仓库规则提交源码和必要文档（排除 `data/`、产物、构建目录）：

```bash
git add config.py main_window.py panels/settings_panel.py panels/sql_panel.py resources/build_info.json resources/style.qss tests/test_theme_responsive.py tests/test_ui.py ui/design_system.py ui/page_chrome.py ui/theme_manager.py docs/plans/2026-07-29-global-ui-foundation.md docs/项目交接/PengToolsV4_UI改造会话交接_2026-07-30.md docs/方案/overview.md
# 仅在确认对应 HTML 审阅文档需要版本化时再单独 git add；不要 git add -A。
git commit -m "feat: establish global interface foundation"
git push origin main
```

> 若 `git status` 显示更多非本轮文件，逐个审查后再决定是否纳入，禁止盲目 `git add -A`。

### 6.2 然后：实施接口排查工作台改造

权威计划：`docs/plans/2026-07-29-interface-debug-workbench-redesign.md`。

必须按 TDD 小步执行，推荐顺序：

1. `tools/interface_debug_store.py`：先增加请求测试纵向分隔条偏好兼容；确认不会写入请求/响应正文或认证信息。
2. `panels/interface_debug_panel.py`：将开始/停止抓包整合为一个状态主按钮；测试连接、恢复系统代理、代理状态保留。
3. 重构左右双栏：左侧抓包/搜索/筛选/会话，右侧概览/请求/响应/请求测试。
4. 请求测试改为编辑器与响应区纵向 `QSplitter`，默认响应区更大；保留 Headers / Params / Body、SSL、分类、导入导出、复制/格式化、保存接口等全部能力。
5. 环境配置、URL 过滤改为弹窗多条列表管理；主界面只留配置入口。
6. 接口库双击回填；历史 URL 的填充/复制/cURL/保存/单条删除走右键；历史清理走带范围、影响数量、二次确认的配置弹窗。
7. 实施自适应与无障碍规则，最后跑定向测试、敏感扫描、离线构建。

接口排查定向测试：

```bash
QT_QPA_PLATFORM=offscreen "C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_debug tests.test_interface_fiddler_workbench -v
```

每一轮用户可验收改动后都必须：测试 → `scripts/scan_release_secrets.py` → `scripts/build_release.ps1` → 核对 `resources/build_info.json` → 提交并推送。

## 7. 绝对不要再踩的坑（强制）

### 7.1 Git / 工作区

1. **不要覆盖或丢弃当前未提交改动。** 当前 UI 第一阶段真实存在于工作区，尚未进 Git。
2. 不要 `git add -A`；仓库有未追踪 HTML、交接、计划等文件，必须按路径精确 add。
3. 不能把 `data/`、安装包 ZIP、`dist/`、`build/`、临时日志、截图提交到 Git。
4. GitHub 分支名在当前 Windows 环境避免 `feat/xxx` 斜杠形式；如需临时分支使用 `feat-xxx`。

### 7.2 公共 UI 基础层

5. **不要再次覆盖 `ui/design_system.py`。** 该文件原有 `apply_button / apply_tree / apply_table / apply_surface` 被大量面板依赖。此前新增密度模块时曾覆盖导致 `DashboardPanel` 等导入失败，测试误判为 PyQt6 不可用并被跳过。后续只允许兼容式合并。
6. 主题/测试总导入块不能把“新增 helper 缺失”的 `ImportError` 当成 PyQt6 缺失而整体 skip。新增待测模块/函数时，优先运行时导入或分离导入，让测试真正红灯。
7. `sidebar_collapsed` 的含义是**启动时偏好**，不是运行期强制状态。只在 `MainWindow` 初始加载设置时应用；不能在 `_apply_settings()` 每次保存时重复收起侧栏。
8. 布尔配置不能直接裸 `bool(value)`；字符串 `"false"` 会错误变成真，必须规范化解析。
9. QSS 新增 token 必须四主题全覆盖；`ThemeManager.render()` 的未解析 token 检查是故意的，不要移除或吞掉异常。
10. 终端颜色继续使用 `TERM_*` Token，**禁止绑回 `CODE_BG`**。

### 7.3 导航与业务安全边界

11. **不要改导航 / Stack 映射或通过数组位置猜测 Stack index。** 当前真实映射见 `AGENTS.md`：nav 8/9 → stack 8；10 → 9；11 → 10；12 → 11。只允许自我学习（nav 8）彩蛋隐藏，日报和需求必须常显。
12. 不破坏 `main_window.py:_show_panel()` 的副作用，尤其离开接口排查时暂停代理、进入/离开回调、设置同步、个人页切换、需求刷新。
13. SSH 连接必须 per-tab，禁止做全局 disconnect；终端退格文本规范化必须保留 `\x08`。
14. 密码不上库；凭据仅 DPAPI/`enc`，禁止新增 b64 存储。
15. 日志导出目标必须 resolve 到具体 `.log`（`resolve_remote_log_file`）；导出名必须是“关键字文件夹 / IP-服务.log”；远端导出走 stdout 流，禁止远端临时文件。

### 7.4 接口排查特别安全约束

16. 抓包请求、响应、Token、Cookie、Authorization、密钥、明文只在内存；**禁止**写入 `data/interface_debug.json`、任意日志、搜索、历史、收藏或新的 JSON。
17. 停止抓包只停止监听，**不得清空会话**；仅用户点击清空和应用退出调用 `clear_session()`。
18. Private 版网络例外仅限本机 `127.0.0.1` CDP / IE MITM 代理；不得连接远程 host，不得监听局域网。
19. IE 代理启动前备份 WinINet；停止、失败、退出必须恢复。证书仅删除配置记录的指纹。
20. 请求测试仅使用已保存环境 Base 替换抓包 URL 的 host，必须保留 path/query；HTTPS 默认校验继续开启。
21. 删除历史、环境、规则等危险操作：取消在左、确认在右、**默认焦点取消**。

### 7.5 验证与打包

22. 不可用“测试跳过”当作通过。此前离屏测试曾因导入失败被错误标记 PyQt6 missing；必须确认执行了真实断言。
23. 打包前后都执行敏感扫描；每轮可验收改动后都用 `scripts/build_release.ps1` 重新打包。
24. 若 PyInstaller 最后写 `dist/PengToolsHub.exe` 报 Permission denied，先检查并关闭所有 `PengToolsHub.exe` 进程；这通常是文件锁，不是代码/依赖问题。不要删除用户数据目录来解决。
25. 构建后必须核对三个产物：`dist/PengToolsHub.exe`、`Installer/PengToolsHub.exe`、`PengToolsHub_Offline_Setup.zip`，再读取 `resources/build_info.json` 确认时间。

## 8. 辅助定位

| 目的 | 文件 |
|---|---|
| 强制项目规则 | `AGENTS.md` |
| 当前 UI 第一阶段计划 | `docs/plans/2026-07-29-global-ui-foundation.md` |
| 接口排查实施计划 | `docs/plans/2026-07-29-interface-debug-workbench-redesign.md` |
| 接口排查主 UI | `panels/interface_debug_panel.py` |
| 接口排查配置 / 安全存储 | `tools/interface_debug_store.py` |
| 接口排查会话视图纯逻辑 | `tools/interface_session_view.py` |
| SSH / 日志主面板 | `panels/ops_log_panel.py` |
| SSH / 导出业务 | `tools/ops_ssh.py`、`tools/ops_ssh_shell.py` |
| 终端控件 | `ui/ssh_terminal.py` |
| 主题 / QSS | `ui/theme_manager.py`、`resources/style.qss` |
| 公共 UI | `ui/design_system.py`、`ui/page_chrome.py` |
| 导航 / Stack / 设置应用 | `main_window.py` |
| 设置兼容与数据根 | `config.py` |
| 当前构建时间 | `resources/build_info.json` |

## 9. 推荐新会话第一条工作指令

> 阅读 `AGENTS.md` 和 `docs/项目交接/PengToolsV4_UI改造会话交接_2026-07-30.md`，保护当前未提交的全局 UI 第一阶段改动；先复验并精确提交，然后按 `docs/plans/2026-07-29-interface-debug-workbench-redesign.md` 的 Task 1 开始接口排查模块 TDD 改造。全程保持抓包敏感数据仅内存、导航映射不变、每轮重新打包。
