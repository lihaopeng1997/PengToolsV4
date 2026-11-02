# PengToolsHub V2 UI 重构（最终版）· 技术设计与实施规格

日期：2026-08-29 · 状态：已批准（视觉稿经三轮原型迭代定稿：`ui_redesign_drafts/v2-daylight-all-modules.html` R3）
授权：用户明确授权「开发期可不遵循 AGENTS.md 硬性要求」「技术层由开发自行设计」。

## 1. 目标与不变量

- 目标：按定稿 V2「白昼玻璃」视觉，重构真实软件 UI。
- **功能不变量**：全部 22 个导航页面、业务联动（需求→发版/日报/DOCX 信号）、托盘/热键/单实例、数据边界全部保持现状。
- 安全自觉保留：密钥不入 `resources/`、数据仅 `config.local_data_dir()`、抓包仅 loopback。

## 2. 技术决策（WebEngine 混合壳）

| 项 | 决策 |
|---|---|
| 渲染 | PyQt6-WebEngine 6.11.0（与 PyQt6 6.11.0 同源锁定） |
| 架构 | 混合壳：侧栏/首页为 Web（V2 视觉），其余 20 页复用现有 QWidget 面板（QStackedWidget 原样） |
| 通信 | QWebChannel + `HomeBridge(QObject)`；Python→JS 用信号 `activeChanged(int)`，JS→Python 用槽 `navigate(int)` 等 |
| 资源 | `resources/webui/*.html`，仅 file:// 本地加载；`WebLocalPage.acceptNavigationRequest` 拦截一切非本地导航（无远程依赖、无 CDN） |
| 回退 | `ui_web_shell` 设置项（默认开）；WebEngine 导入失败或开关关闭 → 自动回退原生侧栏，功能无损 |
| 分层 | `ui/web_shell.py` 不 import panels；数据由 main_window 注入 provider（main_window→tools 合法） |
| AGENTS.md 演进 | 「禁止内嵌浏览器内核」按 AGENTS 第 2 节演进条款放开：本地渲染、禁外链、禁远程调试；本文件即演进记录 |

## 3. 数据流（首页真数据）

`main_window._dashboard_summary_payload()`（main_window→tools 合法）组装：
- 称呼：`config.load_settings()['home_username']`
- 发版：`tools.dashboard_release_items.load_release_items/board` → 总数/完成/百分比/最近目标日
- 最近需求：`tools.requirements.load_requirements()` 前 5 条
- 日报计数：`tools.daily_reports.load_reports()` 本周已提交天数

## 4. Phase 1 范围（本轮）

1. `run.py`：WebEngineQuick.initialize()（条件）。
2. `resources/webui/chrome.html|dashboard.html`：V2 视觉侧栏 + 首页。
3. `ui/web_shell.py`：桥接/本地页/视图工厂；`WEB_SHELL_AVAILABLE` 探测。
4. `main_window.py`：`_create_sidebar` 双页栈（原生/Web）；Stack[0] Web 首页；`_show_panel` 尾部同步高亮。
5. `config.py`：`home_username`、`ui_web_shell` 默认与归一化。
6. `requirements.txt` 锁定 PyQt6-WebEngine==6.11.0。
7. `tests/test_web_shell.py` 定向测试（offscreen 安全：不实例化视图，只测桥/配置/导航 JSON/导航白名单纯函数）。

## 5. Phase 2+（后续迭代，不在本轮）

- 其余面板逐个 Web 化（每模块一个 html + provider 增量迁移）；顶栏横贯式玻璃条（透明覆盖方案）；悬浮工具栏 V2 皮；设置页「首页称呼」字段接 settings_panel；语言切换热更新 chrome；主题（极光深色）双主题。

## 6. 风险与回退

- WebEngine 使安装包 +~150MB → 发布节点重新打包时确认。
- 任何 Web 层异常：`ui_web_shell=false` 或删除依赖即回退原生 UI，业务无损。
- 测试策略：定向测试先行；发布节点按 AGENTS 第 8 节跑权威全量 + 构建。
