# PengToolsHub Engineering Rules

本文件定义 PengToolsHub 的长期工程约束。动态事实以当前 `origin/main` 源码为准；本文件不保存任务进度、当前 SHA、完整导航清单或 Agent 记忆。

长期产品与架构路线：`docs/superpowers/specs/2026-08-30-pengtoolshub-development-master-plan.md`。

## 1. 协作职责

**Agent**：同步 Git → 读取当前任务相关源码 → 做最小正确修改 → 跑最低充分验证 → commit → push → 简短报告 → STOP。Agent 不批准自己的提交，不因“顺便优化”扩大任务。

**External Reviewer / Web 端**：审真实 GitHub commit、parent、diff、架构与风险，判定 PASS/FAIL/READY_FOR_UI_REVIEW，并决定下一任务。

Agent 报告不是代码事实源；`origin/main` 和真实 commit diff 才是。

## 2. Git 是唯一交接源

新任务先执行：

```bash
git status --short
git fetch origin --prune
git switch main
git pull --ff-only origin main
git rev-parse HEAD
git rev-parse origin/main
```

任务会给出 `BASELINE_COMMIT`。必须满足：

```text
HEAD == origin/main == BASELINE_COMMIT
working tree clean
```

否则输出 `TASK_LOCK_FAILED` 并停止。

不得自行使用 `reset --hard`、`clean`、`stash`、`rebase`、`merge`、force push 来制造一致。

Push 前再次 `git fetch origin`。若 `origin/main` 已偏离开工 baseline，输出 `REMOTE_MOVED`，停止，不自行合并。Push 后必须 `HEAD == origin/main` 且工作区 clean。

旧对话、`.workbuddy/memory/**`、服务器 SHARED、stash、截图、临时目录都不是代码基线。默认不读取；只有源码无法回答且当前任务确需历史背景时才按需查阅。

## 3. 修改原则

核心：`smallest correct change`。

禁止无关重构、全文件格式化、顺手升级依赖、顺手统一命名、为一个 import 大搬家、因测试难写而重构产品代码、为无关测试失败修改产品代码。

发现非阻断新问题记为 `OUT_OF_SCOPE`。只有会导致当前实现错误、数据损坏或安全问题时才纳入本轮，并说明原因。

## 4. 架构边界

```text
run
 ↓
main_window
 ↓
panels
 ↓
tools / ui / config
```

* `run.py`：QApplication、WebEngine 初始化顺序、单实例、应用级启动/退出，不放业务逻辑。
* `main_window.py`：导航、Stack、跨模块信号、Web/native Shell、Panel 生命周期、托盘/窗口 wiring。原则：`MainWindow = wiring/controller`。
* `panels/`：页面和业务流程编排。
* `tools/`：无 QWidget 的业务逻辑、协议与数据处理。
* `ui/`：通用视觉组件、Shell、Bridge、桌面交互。
* `config.py`：配置、路径、环境、版本定位。

硬边界：

```text
tools -> 不得 import ui
tools -> 不得 import panels
ui    -> 不得 import panels
```

`ui -> tools` 只允许窄、无 QWidget、无业务页面编排的通用能力。

## 5. Hybrid UI

Vue 3 + TypeScript + Vite + QWebChannel 默认负责：Chrome/Sidebar、Dashboard、通知/概览等轻展示型全局 UI。

PyQt QWidget 默认负责：数据库工作台、SQL、SSH/终端、抓包、大型树/表格/Splitter、文件和系统级桌面交互、重业务工具页。

不得因为 Vue 已接入就主动迁移全部 QWidget。

Web UI 是桌面渲染层，不是业务应用。Vue 不直接读写本地文件、数据库，不直接调用业务 Python，不自建公网服务，不加载 CDN/在线字体/remote JS/CSS。

## 6. Bridge 规则

Bridge 是 UI adapter，不是 business service。

* 位于 `ui/`；
* 不 import `panels`；
* 由 MainWindow/provider/callback 注入数据；
* Python producer 与 TypeScript adapter 同步 DTO；
* API 增长后按页面域拆分（目标：ChromeBridge / DashboardBridge），不建设 God Bridge。

## 7. UI authority

### Theme

Python `ThemeManager` 的语义 token 是产品主题权威。Frontend 不得长期维护冲突的独立产品色板。Chrome 与 Dashboard 共用 frontend semantic tokens。

### Responsive

产品断点语义以 `ui/layout_metrics.py` 为准。Frontend 不建立冲突断点。Chrome 后续正确消费 layout mode、sidebar collapsed、language、theme、active nav。

### Navigation

导航唯一权威：`ui/navigation_model.py`。菜单行只显示 `icon + one primary label`；分组双语可保留；禁止“中文 + 英文 + 缩写”；可见项必须有图标；tooltip 只补充说明。

### Workbench

Sidebar 已说明模块身份时，内容区默认不重复大型模块标题。首屏优先：

```text
toolbar/context → workspace → result/detail/command → status
```

Header 只有承载真实上下文、状态或主操作时才保留。

## 8. 数据、安全、用户环境

禁止删除/清空/移动用户 `data`，覆盖真实配置，把密码、Token、Cookie、私钥、抓包正文、解密明文写入仓库或普通日志，为测试写真实 DB/Redis/SSH，或 kill 用户正在使用的 PengToolsHub。

若 GUI smoke 会干扰用户，停止复杂桌面自动化，改用自动验证 + 用户人工 UI 验收。临时测试进程任务结束前清理。

## 9. 测试哲学

目标：`minimum sufficient evidence`。

没有修改的子系统默认不测试。没有明确 regression 默认不新增测试。普通 regression 最多新增 1–2 个精准测试；并发、生命周期、安全、数据迁移、终端核心等高风险任务可以更完整。

禁止每轮 `unittest discover`、每轮打包、UI 小改构建大型 GUI 自动化、用大量源码字符串断言代替产品行为、为测试通过修改无关代码。

无关已有失败记 `EXISTING_FAILURE`，不要扩任务。

## 10. TEST_LEVEL

* **L0 文档/规则**：diff + Git 状态；不跑产品测试。
* **L1 UI/CSS/Vue presentation**：typecheck + 必要 build；生产 embedded 变更时 verify；视觉交用户，结果通常 `READY_FOR_UI_REVIEW`。
* **L2 普通业务/Panel**：最相关 targeted tests；明确 regression 最多新增 1–2 个。
* **L3 runtime/并发/lifecycle/IPC/WebEngine/SSH terminal core**：targeted regression + 必要最小 runtime smoke。
* **L4 dependency/package/release/security**：仅相关任务执行 full isolated suite、audit、secret scan、release build、package smoke。

## 11. Runtime smoke

默认仅在 `run.py`、`ui/web_shell.py`、`ui/single_instance.py`、capture lifecycle、thread/process lifecycle、QWebChannel readiness、SSH terminal core、packaging runtime 等风险区需要。普通 CSS、文案、图标、文档、纯业务函数不默认启动整程序。

## 12. 依赖和生成物

不得顺手升级依赖。新增/升级必须检查兼容性、许可证、Python/Node、打包体积、transitive constraints 和安全影响。禁止 `npm audit fix --force` 和无约束 `pip upgrade`。

Vue 源码：`frontend/**`；生产 embedded：`resources/webui/vue/**`。修改生产 Vue 后重新生成，同 commit 提交；不得手改 bundle。

## 13. 性能与生命周期

性能优化先测量再决定。长期保持 heavy panel 懒加载；非当前 Panel 无意义 timer/polling 暂停或降频；用户明确建立的 SSH/DB 会话不因切页擅自断开；Panel dispose 停止自己的 timer/thread/process/socket；长生命周期 buffer/cache 有上限；普通业务 Panel 不新增独立 WebEngine View。

性能全量测量只在专门 PERF 任务或发布里程碑执行。

## 14. SSH / 终端专项

SSH/终端属于核心重交互能力，默认保留 PyQt + Paramiko 链路。

长期方向：

```text
TerminalTransport
    ↓
TerminalEmulator / ScreenModel
    ↓
TerminalView
```

不得继续通过无限增加 `QTextCursor` 特判来假装完整终端兼容。transport 不应为了 UI 提前丢弃 screen model 需要的 ANSI/CSI cursor 信息。优先修 shell 行编辑可靠性，再提升 TUI 兼容。

未经专项评审，不默认引入 xterm.js + 新 WebEngine 终端。终端正文、密码、Token 不进入持久化日志。

## 15. 冻结区域

除非任务明确涉及，否则不要修改：capture lifecycle、WebEngine sandbox policy、single-instance ownership、packaging strategy、dependency pins、用户数据路径、navigation authority、Bridge contract、SSH transport/session ownership。

发现问题报告 Reviewer，不以“顺便优化”为理由进入冻结区。

## 16. 提交和报告

每任务原则上一个逻辑 commit。提交前只检查：scope correct、required tests pass、forbidden area untouched、generated artifacts synced if required。

最终报告只需要：

```text
RESULT:
COMMIT:
CHANGED:
TESTED:
KNOWN_LIMITATION:
```

允许状态：`IMPLEMENTED`、`READY_FOR_UI_REVIEW`、`PASS`、`BLOCKED`、`TASK_LOCK_FAILED`、`REMOTE_MOVED`。

Push 后 STOP。

## 17. 最终原则

```text
Git is truth.
Read only what the task needs.
Change only what the task needs.
Architecture before convenience.
Function before decoration.
Test only what the change risks.
Push one reviewable commit.
Then stop.
```
