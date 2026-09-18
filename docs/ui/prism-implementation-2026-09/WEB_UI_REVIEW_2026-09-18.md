# Web 代码复核：设置保存与 Prism 主窗

仓库：`lihaopeng1997/PengToolsV4`，目标分支：`ui/prism-v1`。
变更基线：`ce01d8166ed94f411bbd4d917cff938dc887fbc8`。请先报告实际读取的完整提交 SHA，并只审查该基线到本次 UI 提交的差异。

用户授权将本轮纯代码改动发布给 Web GPT 复核。本地原有 13 个遗留文件、此前数据中心 Agent 未完成包及截图不在本次提交范围内。不要推断未提交内容；也不要要求删除这些文件来制造干净工作区。

## 目标和依据

- 采用单一 `calm` 主题，按 `docs/ui/concepts-2026-09/prism-suite/UI优化需求_晴空棱镜_Agent实施规范.md`。
- 自绘标题栏已获用户批准，以 `PRISM_SHELL_REVISION_2026-09-15.md` 为准，不能用旧的禁止 frameless 断言要求回退。
- 应用保存与内网模型保存分域，不覆盖模型/提醒草稿；有真实忙碌、互斥及成功/失败回执。点击后保存点击时的快照。
- 主窗接入标题栏、现有 File/View/Help 动作、模块标签及统一导航弹层，保留原有业务流程。导航和权限来自 `navigation_model`，不增加原型中的模拟业务入口。

## 重点检查

1. `panels/settings_panel.py`、`MainWindow._apply_settings/_apply_settings_impl`：两个持久化域是否独立，提醒保存是否在回调重载前完成，异常和部分成功是否如实呈现，延迟提交是否保留快照，失败和重复点击是否安全。
2. `MainWindow._setup_ui_shell/_show_panel/_on_module_tab_close`、`ui/module_tabs.py`：成功导航才建立标签，首页不可关闭，当前标签回邻近或首页；创建失败及重复失败不得删除可见标签；标签关闭不销毁缓存页面或数据库会话。
3. `ui/prism_title_bar.py`、`ui/prism_window_frame.py`、主窗 `nativeEvent`：未处理消息返回 `(False, 0)`，不调用曾在本机触发 access violation 的 PyQt6 父类 nativeEvent；保留 Qt 原关闭/托盘/确认链。置顶只作用于当前窗口，不新增持久化键。
4. `frontend/src/chrome/`、共享 bridge、`ui/web_shell.py` 和 `ui/prism_sidebar.py`：72/248px 窄 WebView 用真实 Qt 弹层逃逸裁切；能力声明时序、坐标映射、非法矩形拒绝、权限入口、语言/选中同步、Esc/焦点恢复及原生 fallback 是否完整。
5. 作者双击入口、账户菜单、侧栏折叠、帮助/退出是否在 Web 与原生模式都真实可达。不要将隐藏旧控件当作功能保留。
6. Vue 生成资源与源码是否配套；架构依赖不跨越 `tools -> ui/panels` 禁区；不读取或改写用户凭据、业务数据及现有会话。

## 已有证据和限制

详见 `UI_SETTINGS_MENU_REPAIR_2026-09-17.md`，按静态、隔离行为和真实 Windows 运行证据分别判断。截图、真实 Snap/混合 DPI 和用户视觉验收未包含在这个代码复核包中；不能仅凭源码认定这些通过。

主代理已独立运行隔离主窗/设置/导航/标题栏/标签/框架/架构共 39 项，以及独立 Windows WebEngine 专项 1 项，通过。实际 Windows 无业务主窗 `show/processEvents` 烟测通过。提交前的最后增量验证会另记录，不把跳过或崩溃算作通过。

已知无关失败：旧 `tests.test_web_shell.JsHandshakeOrderTest.test_dashboard_page_ready_after_render` 以源码字符串首次位置判断握手顺序；对应 dashboard 源码和测试与基线无差异，本包未修它。旧全量主窗测试的业务初始化 fixture 不代表本包隔离验证。

请输出可行动问题：严重程度、文件/行号、具体触发条件、实际后果及最小修复方向。区分确认缺陷、需要运行证据的疑点及视觉待验收，不重复旧主题/旧窗口决定。不自动修改、提交或部署；主代理读取结果后安排修正和复核。
