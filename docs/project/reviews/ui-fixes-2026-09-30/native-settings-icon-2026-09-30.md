# 原生侧栏设置图标续修（2026-09-30）

状态：源码修复已复核，定向 Qt 测试通过；尚未入包，用户视觉接受仍单独验收。

- 基准：`dev/2026-09-23` @ `818fba72bf437de9bd49fcd5b198fb1270096a53`，保留全部原 dirty 变化，不提交、推送或整体暂存。
- 根因：Web 导航模型设置叶子的图标 role 为 `gear`，原生 `ui/icons.py` 注册的是 `settings`。`PrismSidebar._make_leaf_button()` 原样调用 `apply_icon(..., 'gear')` 导致设置 QIcon 为空；在收起态按钮只有图标时形成空白入口。
- 最小修改：仅在原生设置叶子且 role 为 `gear` 时映射为 `settings`。不改变 Web DTO、导航索引 7、语言标签、权限或点击回调；其他注入 role 原样保留。分类 popup 没有发现需要同步修复的设置叶子。
- 修改文件：`ui/prism_sidebar.py`、`tests/test_prism_sidebar_menu.py`；主代理已对照本轮原字节快照复核增量。产品差量为 4 行新增、1 行删除，无前端资源改动。
- 验证：`QT_QPA_PLATFORM=offscreen python -m unittest tests.test_prism_sidebar_menu.PrismSidebarMenuTests.test_settings_still_navigates_as_a_real_leaf -v` exit 0、1/1。中文展开、英文收起、英文重展开分别验证 QIcon 非空、20 × 20、tooltip 以及实际点击恰好派发一次 7；输入 DTO 仍为 `gear` 且不变。
- Scoped `git diff --check` exit 0；未重复全套菜单测试、typecheck/build 或打包。此证据是隔离 Qt fixture，不是 UI3 包窗口验收。
- AGY 限定源片段只读复核确认命名差异；其编辑路线已因 headless `command` 权限无法提示而自动拒绝，未绕过权限。Grok 保持暂停，备用 GPT-6 worker 完成编辑与 self-test，主代理审阅日志和差量。
- 快照/patch/日志：`C:\Users\Lenovo\AppData\Local\Temp\pengtools_native_sidebar_icon_fix_20260930_e157f83a413448e1a9b7e41c4961d970\`；最终补齐三种状态实际点击的日志为 `test-settings-native-icon-review.log`。

本修复与上轮 `main_window.py` 侧栏缓存同步补丁都不在既有 UI3 EXE 中。UI2/UI3 隔离目录与用户新数据保留。
