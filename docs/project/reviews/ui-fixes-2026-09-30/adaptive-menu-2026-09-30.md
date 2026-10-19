# 菜单收起/展开两态证据（2026-09-30）

- 基准：`dev/2026-09-23` @ `818fba72bf437de9bd49fcd5b198fb1270096a53`。AGY 两次未完成限定编辑后，剩余两态菜单改动由 GPT-6 Luna worker 完成；没有使用权限绕过。
- 目标行为：72/84 px 收起态按可见类别显示唯一图标，类别菜单选择精确真实模块；248 px 展开态直接平铺所有叶子。收起/展开后活动类别与叶子选择保持一致。
- 通过：`tests.test_prism_sidebar_menu` 6/6、shell contract 定向用例、Windows QPA 生产 Chrome fixture 1/1；fixture 覆盖 72 px 类别图标、datacenter/ops bridge 请求与 248 px 直接叶子。`npm --prefix frontend run typecheck`、`build:embedded`、`verify:embedded` 及 owned-file `git diff --check` 通过。
- 未验：真实用户应用之外的人工视觉验收尚未进行。`NativeSidebarGeometryTests.test_compact_navigation_and_footer_remain_fully_reachable` 在旧 `window.nav_buttons` 的 960 px 断言失败（125 px 控件超过 72 px 视口）；其与本次改动的归属/因果未确认，未修改该区域。

以上是合成与隔离生产 DOM 证据，不等同用户视觉签收。UI3 包审计见同目录 `ui3-package-audit-2026-09-30.md`。

## 本次续接增量

- UI3 实窗已观察到收起类别菜单、MySQL 精确选择、展开直接叶子及需求管理直达；后续正常退出已确认，用户最终视觉签收仍待确认。详见 [UI3 实窗续验](ui3-runtime-acceptance-2026-09-30.md)。
- 上述 960 px 失败已归因为主窗口保留了侧栏重建前的控件缓存，同时测试的“两态按钮身份不变”不适用于新类别要求。`MainWindow._on_layout_mode()` 在 `set_icon_only()` 后补一行索引同步；更新测试检查当前附属控件与实际几何，不改变导航 ID 或面板装配。
- `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_prism_native_sidebar_geometry -v` exit 0、1/1，通过四种宽度及往返折叠/展开；worker 的差量和日志由主代理复核。该最小 Python 补丁保留未提交，尚未进入既有 UI3 EXE。没有重复菜单 6/6、typecheck 或 embedded build。
- 第二次续修另补原生设置叶子的 `gear` → `settings` role 适配；中文展开/英文折叠及重展开分别检查实际点击、图标与 DTO 不变，定向 1/1。见 [设置图标报告](native-settings-icon-2026-09-30.md)。此补丁同样尚未进入 UI3 EXE。
