# Prism 壳层视觉修复记录（2026-09-18）

状态：实现完成，主代理已查看隔离 Windows WebEngine 整窗截图；用户实际窗口与原生后备首页尚未验收。\
仓库：`D:/PengTools`；分支：`ui/prism-v1`；本次读取的基准 HEAD：`cea4a63c7d77660c3d40f3543165718ea956073f`。\
本记录只描述本包的展示层修复；没有提交、暂存、切换、重置或清理 Git 状态。

## 问题与目标

真实运行壳层的 ModuleTabs 原先挂在中央根布局，横跨侧栏和工作区，形成“标题栏 → 首页标签 → 侧栏/工作区”的层级；工作区内又有 ContextHeader 和页面内容，和批准原型的 `globalbar → open-tabs → canvas` 不一致。标题栏已经显示 `PengToolsHub` 时，展开侧栏再次显示同一产品名；批准原型的展开轨道文案是“个人工作空间”。

Hero 主视觉按当前批准 V2.1 源码核对：生产 `HeroSection.vue` 已是 120×120 图形、4.8 秒 `translateY(0/-5/0)`、`rotate(-5/5/-5deg)`、`scale(1/1.04/1)`，并保留 reduced-motion、隐藏页面暂停和已有主题 token。原型旧 `suite.css` 的 130×115 `orbit` 写法不是本轮重画依据，因此没有凭截图猜测或改造艺术图形。

## 实施前后

实施前：

```text
titlebar
module-tabs  ← 横跨整个 shell，包含侧栏宽度
shell-body
  ├─ sidebar
  └─ content
       ├─ ContextHeader
       └─ page-body → stack
```

实施后：

```text
titlebar
shell-body
  ├─ sidebar
  └─ workspace/content
       ├─ ContextHeader        ← globalbar
       ├─ ModuleTabs            ← open-tabs
       └─ page-body → stack     ← canvas
```

`ModuleTabs` 仍使用原有导航 ID、激活/关闭信号和面板缓存；关闭标签仍由 `MainWindow` 选择邻接页面，不销毁面板或改变业务会话。标题栏的语言菜单、用户菜单、作者双击解锁和折叠按钮继续由现有 host controls 保持可达。

侧栏 native fallback 和 Vue chrome 的展开品牌文案改为“个人工作空间”；native fallback 在 English 下切换为 `Personal workspace`，并随既有语言更新路径刷新。标题栏产品名保持 `PengToolsHub`。

## 修改文件

- `main_window.py`：将 ModuleTabs 从 root layout 移入 `content_area`，插在 ContextHeader 和 page-body 之间。
- `ui/prism_sidebar.py`：native 展开品牌文案及中英文切换。
- `frontend/src/chrome/ChromeApp.vue`：Web 侧栏展开品牌文案。
- `resources/webui/vue/chrome.html`、`resources/webui/vue/assets/chrome-*.js`：由 Vite 从 Vue 源码重建的正式嵌入产物。
- `tests/test_prism_main_shell_integration.py`：增加工作区层级/顺序回归断言。
- `tests/test_prism_sidebar_chrome.py`：增加 native 品牌文案与语言切换断言。

## 验证

- `python -m unittest tests.test_prism_main_shell_integration -v`：4 项通过，退出码 0；真实生产 `MainWindow` shell 方法的隔离 fixture 检查 ModuleTabs parent 与 layout index。
- `python -m unittest tests.test_prism_module_tabs -v`：7 项通过，退出码 0；导航 ID、激活、关闭、语言和上下文菜单契约保持。
- `python -m unittest tests.test_prism_sidebar_chrome -v`：11 项通过，退出码 0；native 侧栏几何/父级折叠/手动折叠，以及 Web chrome 静态契约通过。
- `npm --prefix frontend run typecheck`：通过，退出码 0。
- `npm --prefix frontend run build:embedded`：通过，退出码 0；仅从 `frontend/src` 重建正式资源。
- `npm --prefix frontend run verify:embedded`：通过，退出码 0；双入口、相对资源、离线引用和 WebChannel contract 通过。
- `git diff --check`（本包文件）：通过；仅有仓库既有 LF/CRLF 提示。

## 边界

本包没有连接数据库、模型、SSH、抓包服务，没有读取用户配置、凭据、数据库或真实业务数据，也没有触碰用户实际运行中的 Windows app PID 41488。没有启动真实业务服务或调用生产窗口。

主代理/Luna 负责独立 Windows WebEngine 生产 shell/chrome/dashboard fixture 与截图验收。本包未宣称用户实际窗口的拖动、缩放、Snap、多屏、DPI 和最终视觉截图已通过；这些项目仍待主代理查看隔离产物后记录。Hero 只完成批准源码核对，未凭空改图形。

## 主代理复核补记

- 已查看 `scripts/diagnostics/prism_visual_acceptance/production_shell_1440x900.png` 和 `production_shell_960x640.png`：真实 Windows QPA、DPR 1.5，生产壳层及正式 Chrome/Dashboard bundle，业务数据为内存示例。标签仅占右侧工作区，展开侧栏不再重复产品名称；不能据此声称真实数据库或用户窗口验收通过。
- 用户实际窗口 2026-09-18 09:08 启动诊断显示 `ui_web_shell_setting=False`、`dashboard=native`、`main_shell=native`。这解释其首页与 Web 示例不同；未更改该用户配置，未关闭或重启现有窗口。原生后备首页仍须独立复核。
- 数据中心接入核对固定于基准 SHA：`main_window.py` 仍创建 `AiWorkbenchPanel`（Oracle/MySQL/OceanBase/达梦）及既有 Redis/Mongo 面板，没有接入新的 `ui/data_center`。DBX 借鉴记录见 `docs/project/DATA_CENTER_AGENT_REQUIREMENTS.md:42`；新 Agent/元数据宿主与事件面板的模拟实现仍未提交，不代表统一工作台已改造。
- Grok 调用没有产出产品改动，不报告 Grok 修复成功。本包实际由 Luna Max 实施，主代理负责范围、差异复核与截图检查。浏览器策略阻碍 Web GPT 页面访问，尚无 Web GPT 审查结果。
