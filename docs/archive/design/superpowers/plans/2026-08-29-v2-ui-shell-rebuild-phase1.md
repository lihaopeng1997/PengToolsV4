# Phase 1 实施计划 · V2 UI 混合壳

执行顺序（每步可独立验证）：

1. config.py：`home_username`（默认 'Lihp'）与 `ui_web_shell`（默认 True）加入 DEFAULT_SETTINGS 与 normalize_settings。
2. ui/web_shell.py：`WEB_SHELL_AVAILABLE`、`WebLocalPage`（本地导航白名单）、`HomeBridge`（navigate/openPalette 槽、activeChanged 信号、navModel/dashboardSummary/homeUsername 槽、set_nav_model/set_summary_provider/set_username 注入点）、`create_chrome_widget/create_dashboard_widget` 视图工厂（QWebChannel 注册 'bridge'）。
3. resources/webui/chrome.html：V2 侧栏（logo 渐变方块 + 分组导航 + 折叠组 + 设置；qwebchannel 桥；点击→bridge.navigate；activeChanged→高亮；时钟）。
4. resources/webui/dashboard.html：V2 首页（问候+称呼、统计瓷砖、最近需求、发版进度、常用工具瓦片；bridge.dashboardSummary 拉数据；工具瓦片→navigate）。
5. main_window.py：
   - `_create_sidebar`：web 启用时返回 QStackedWidget 容器（0=原生侧栏、1=chrome），`self._chrome_bridge` 建立并注入 navModel（含彩蛋解锁态）；
   - stack host[0] 前插 DashboardWebView（web 模式隐藏原生 dashboard_panel，保留信号对象）；
   - `_show_panel` 末尾 `self._web_notify_active(index)`；
   - `bridge.navigateRequested → self._show_panel`；`paletteRequested → self.quick_panel.show_panel()`（ensure_services 后可用，空安全）；
   - `_dashboard_summary_payload()` 数据组装。
6. run.py：WebEngine 可用且设置未禁用时 `QtWebEngineQuick.initialize()`（QApplication 之前）。
7. requirements.txt：`PyQt6-WebEngine==6.11.0`。
8. tests/test_web_shell.py：可用性标志、navModel JSON（含 0/7/14/15/18、彩蛋过滤）、summary provider 环通、config 默认、本地导航白名单（纯函数）。
9. 验证：`QT_QPA_PLATFORM=offscreen python -m unittest tests.test_web_shell tests.test_architecture_boundaries tests.test_core -v` → 人工冒烟 `python run.py`（用户执行）→ commit & push → `codegraph sync .`。

验收：侧栏 V2 视觉、首页真数据、21 个原生面板全部可从新侧栏进入且行为不变；关闭 `ui_web_shell` 后与升级前完全一致。
