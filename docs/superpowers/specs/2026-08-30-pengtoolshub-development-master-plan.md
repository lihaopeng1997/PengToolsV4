# PengToolsHub 后续开发总纲

> 状态：长期路线文档  
> 建立日期：2026-08-30  
> 审阅代码基线：`8b2b0ab02390aa1c56fc11aeb604ea512d1b1d53`  
> 代码事实源：始终以当前 `origin/main` 为准，本 SHA 仅表示制定方案时的审阅点。

## 1. 目的与职责

本文件固化 PengToolsHub 后续产品、技术、UI、性能和开发路线，避免依赖某个 Agent 对话或人工记忆。

- External Reviewer / Web 端：架构判断、任务拆分、真实 Git diff 审查、PASS/FAIL、下一步规划。
- Agent：从 Git 同步、实现当前任务、最低充分验证、commit + push、STOP。
- `origin/main`：唯一代码交接事实源。
- `AGENTS.md`：长期工程纪律。
- 本文件：长期架构与产品路线。

若本文与当前源码冲突，由 Reviewer 判断是方案尚未实施、文档过期还是发生架构漂移。

## 2. 产品目标

PengToolsHub 是 Windows 离线开发/运维桌面工具台。

目标：启动快、本地数据安全、数据库/SSH/抓包/SQL/文件等重交互优先可靠、UI 统一但不强行全 Web、heavy module 按需加载、后台任务生命周期明确、开发采用小提交和定向验证。

优先级：

```text
正确性 > 用户数据与安全 > 核心工具可用性 > 架构一致性 > 资源与性能 > UI 一致性 > 装饰
```

## 3. Hybrid Desktop 目标架构

```text
run.py
  ↓
main_window.py
  ↓
panels/
  ↓
tools/ | ui/ | config.py
```

Python：`run.py` 管 bootstrap；`main_window.py` 管 Shell/导航/Stack/wiring；`panels` 管流程；`tools` 管纯业务与协议；`ui` 管通用 UI/Bridge；`config` 管配置路径。

`MainWindow = wiring/controller`，禁止继续吸收 Redis、SSH、SQL 等业务实现。

Vue 默认负责 Chrome/Sidebar、Dashboard、通知/概览等轻展示。

PyQt QWidget 默认保留数据库工作台、SQL、SSH/终端、抓包、大型树/表格/Splitter、文件与系统集成。

**不做全量 QWidget → Vue。**

## 4. Web / Native Contract

Bridge 是 UI adapter，不是业务 service。

长期目标：ChromeBridge 管 nav/shellState/openPalette；DashboardBridge 管 summary/actions/refresh；共同 pageReady。Bridge 位于 `ui`，不 import `panels`，由 MainWindow/provider/callback 注入数据。

Web UI 只加载本地资源，禁止 CDN/在线字体/remote JS/CSS，不为 UI 启动无必要 HTTP server。

## 5. UI 设计系统

### Theme authority

Python `ThemeManager` 的语义 token 是主题权威。当前 Vue Chrome/legacy Dashboard 的独立硬编码色板长期必须收口。目标给 Web 提供最小 Theme Snapshot，并由 Chrome/Dashboard 共用 frontend semantic CSS variables。

### Responsive authority

以 `ui/layout_metrics.py` 为准：minimum 960×640、recommended 1180×720、Wide ≥1440、Standard 1280–1439、Compact 1100–1279、Narrow 960–1099。Vue 不建立冲突断点。

### Navigation

`ui/navigation_model.py` 是唯一权威。菜单只显示 `icon + one primary label`；分组双语可保留；不再出现中文+英文+缩写；可见菜单必须有图标。

### Workbench

Sidebar 已说明模块身份时，不重复大型标题。首屏优先：

```text
toolbar/context → workspace → result/detail/command → status
```

## 6. SSH / 类 Xshell 终端专项

### 6.1 当前链路

```text
ui/ssh_terminal.py
  QPlainTextEdit
       ↑
tools/ops_ssh_shell.py
  InteractiveShell
       ↑
Paramiko invoke_shell(PTY)
```

已有正确方向：per-tab SSH Client/PTY、`xterm-256color`、Backspace/Delete/Enter/Tab/方向键/Home/End/部分 Ctrl 映射、输出 buffer 上限、inactive UI 降刷新、PTY 关闭/错误基础处理。

历史文档已识别一个问题：SSH 连接成功不等于 PTY ready，假连接会让终端看似可输入但实际无活通道。

### 6.2 用户当前反馈与结构性问题

用户反馈：直接在控制台输入命令不好用，退格/删除命令不好用。

不能只通过“发送了 `\x7f` / `ESC[3~`”判终端正确。

当前 `_map_key()` 会发送控制序列，但 `ops_ssh_shell.strip_ansi()` 会剥掉绝大多数 CSI/ANSI，仅保留少量 EL/ED；随后 `_SshTerminalView` 用 `QTextCursor` 在文档尾部模拟 CR/BS/DEL。系统没有真正的 cursor(row,col) / terminal screen buffer。

因此简单行尾退格可能工作，但这些场景天然容易错位：

* 光标移到命令中间修改；
* Delete 删除光标后字符；
* Home/End；
* history 调出旧命令再编辑；
* Tab completion 重绘；
* 宽字符/中文；
* `less/top/vim` 等 cursor-addressing 程序。

**不要继续无限扩大 `_render_terminal_text()` 的 if/else。**

### 6.3 T0：会话状态可靠

SSH connected 与 PTY ready 分离；只有 `shell_alive` 才允许输入；PTY 失败/关闭明确失活；每 tab 隔离；断开只释放当前 tab。

### 6.4 T1：Shell 行编辑

稳定支持字符、Backspace、Delete、Left/Right、Up/Down history、Home/End、Ctrl+A/E/U/W/K/C/D/Z/L、Tab completion、多行 paste、resize。

验收必须验证最终远端回显/屏幕状态，不只断言发送 bytes。

### 6.5 T2：Terminal Screen Model

```text
TerminalTransport
  Paramiko bytes

TerminalEmulator / ScreenModel
  ANSI/CSI
  cursor
  erase
  attributes
  scroll

TerminalView
  Qt render
  selection/copy/search
```

transport 不提前 strip 掉 screen model 所需 cursor 信息。

实施前比较：成熟轻量 Python VT/ANSI emulator core vs 自研最小 emulator。优先评估成熟库，但新增依赖前检查许可证、维护状态、Python 3.12、性能和打包体积。

不默认选择 xterm.js + 新 WebEngine 页面。终端是重交互工具，为它新增 JS terminal、Bridge、Web 生命周期会增加内存和故障面。

### 6.6 T3：高级 TUI

T2 稳定后再逐步评估 `less/top/vim`、SGR/256 色、bracketed paste、alternate screen、mouse events。先把 shell editing 做可靠，不冒充完整 xterm。

### 6.7 SSH 性能

scrollback 有界；inactive tab 降低 UI 刷新但不丢状态；大量输出批量 model update；resize_pty debounce；reader thread 可退出/join；tab close/disconnect 不留 client/channel/thread；终端正文不持久化日志。

## 7. 软件占用和性能

原则：先测量，再优化。

### Startup

保持 Shell 先显示、heavy panel 懒加载、不在启动时构造全部数据库/抓包/SSH。

建立 baseline：process start→窗口可见、process start→web_shell_ready、首页 idle memory。后续避免明显回退，不在无基线时拍脑袋写绝对毫秒指标。

### WebEngine

默认只有 Shell/Dashboard 使用生产 WebEngine View；普通业务 Panel 不新建 WebEngine；不为小组件创建隐藏 Web 页面。STEP 6 后测量两个 View 的常驻成本，有证据超预算再评估合并/按需生命周期。

### Panel lifecycle

逐步形成 `on_panel_activated()` / `on_panel_deactivated()` / `dispose()` 约定。非当前 Panel 无意义 polling/timer 暂停或降频；用户建立的 SSH/DB 会话不因切页擅自断开；Panel dispose 停止 timer/thread/process/socket。

### Buffer / Cache

SSH scrollback、capture session、日志预览、Dashboard recent、搜索缓存等长期 buffer 必须有上限。

### 大模块

不 big-bang 拆文件。只有修改相关模块时按 DTO/state、worker、protocol adapter、pure formatter、reusable UI component 等稳定切面渐进抽离。

## 8. 数据与安全

用户数据由 `config.local_data_dir()` 管理；升级不覆盖/删除 data；密码/Token/私钥不进仓库与普通日志；SSH 允许用户直接输入远端命令但软件不得自动执行命令草稿；SQL/Postman/cURL 的生成与执行保持边界；自动测试不连真实生产 DB/Redis/SSH；Agent 不操作用户真实运行实例。

## 9. Lean 测试

规则见 `AGENTS.md`。核心是 `minimum sufficient evidence`：L0 文档只检查 diff/Git；L1 UI typecheck/build/verify + 用户视觉；L2 普通业务 targeted；L3 runtime/IPC/WebEngine/SSH core targeted regression + 必要 smoke；L4 发布/依赖/安全才 full suite/audit/build。

## 10. 推荐路线

### G0 — Governance（当前）

本总纲进入 Git；`AGENTS.md` 切 Lean Git Workflow；新 Agent 只从 `origin/main` 接手。

### STEP 5 — Vue Dashboard

legacy Dashboard → Vue，继续用现有 summary provider，不动业务 Panel，最终用户视觉验收。

### STEP 6 — Web UI Contract

Theme Snapshot、semantic frontend tokens、responsive/collapsed ShellState、ChromeBridge/DashboardBridge 拆域、Chrome/Dashboard 共用视觉 token。

### SSH-1 — 终端可靠性 P0

优先级高，属于核心功能问题。复核 PTY ready；建立真实 shell 行编辑复现矩阵；区分 transport、TERM/stty、display model 根因；修证据明确问题；不继续扩大 QTextCursor ANSI 模拟。

### SSH-2 — Terminal Screen Model

在 SSH-1 证据基础上做 transport/emulator/view 分层。首要目标 readline 编辑可靠，不一次追完所有 xterm 特性。

### PERF-1 — 性能基线

记录 startup visible、web_shell_ready、idle memory、打开 Redis/SSH/capture 后增量、关闭/离开后的 thread/timer/process 状态，只修真实热点。

### UX-1 — Workbench 批量人工验收

按 DB、SSH/日志、接口排查、需求/发版、开发工具分域，一次收集 3–5 个 UI 反馈再做 polish，避免一个像素一个 commit。

### RELEASE

正式发布才执行 full isolated suite、dependency/security audit、secret scan、ONEDIR build、package smoke、用户 data 升级保护验证。

## 11. 明确不做

不全量 QWidget→Vue；不让 Bridge 变 business service；不默认给 SSH 加 xterm.js/WebEngine；不为普通 UI 改动跑全量/安装包；不无证据升级依赖；不大范围重写 MainWindow；不为减行数机械拆模块；不自动执行生成的 SQL/命令；不用 Agent memory 替代 Git。

## 12. Reviewer 审查

所有任务检查真实 `main` SHA、parent、scope、架构漂移、generated artifacts。UI 重点看数据契约、Theme/Responsive、信息冗余，再交用户视觉。Runtime/SSH/Capture 重点看状态机、race、thread/process/socket、failure path、cleanup、用户环境副作用。Dependency/Release 看 exact pins、transitive compatibility、audit、ONEDIR、user data 和 package runtime。

## 13. 最终原则

```text
Git is truth.
Architecture before convenience.
Function before decoration.
Heavy tools stay native unless evidence says otherwise.
Web UI shares one product semantic system.
Measure before performance optimization.
A terminal needs a terminal model, not endless text-edit patches.
Test only what the change risks.
One reviewable change, then stop.
```
