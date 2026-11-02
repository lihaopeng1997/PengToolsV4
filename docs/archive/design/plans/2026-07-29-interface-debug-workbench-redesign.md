# 接口排查整体工作台改造 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不改变接口抓包安全边界及既有核心能力的前提下，将接口排查模块改造成已确认的左右双栏诊断工作台，并重构请求测试、环境、过滤与历史管理交互。

**Architecture:** 保持 `InterfaceDebugPanel` 作为界面编排层，复用 `interface_session_view` 的筛选与会话视图逻辑、`interface_debug_store` 的非敏感配置持久化，以及现有请求测试和接口库能力。先抽离可测试的配置与历史操作规则，再调整 PyQt6 布局、弹窗和右键菜单；不把捕获报文、Cookie、令牌或请求/响应正文写入配置文件。

**Tech Stack:** Python 3.12、PyQt6、unittest、现有 QSS 主题令牌、`tools/interface_debug_store.py`、`tools/interface_session_view.py`。

---

## 设计确认与不可变约束

### 已确认的视觉与交互结构

1. **整体双栏工作台**：左侧用于抓包、搜索、筛选、会话定位与会话管理；右侧用于概览、请求、响应和请求测试。主分隔条可拖动，窗口变窄时左栏优先隐藏，不挤压右侧内容。
2. **抓包控制区**：开始/停止合并为同一状态按钮；保留“测试连接”和“恢复系统代理”；代理状态始终可见。停止抓包只停止监听，不清空会话，清空仅由用户显式触发或应用退出触发。
3. **会话定位区**：保留 URL 搜索、XHR/Fetch 等筛选、会话导出与清空。请求列表保留全部数据字段，视觉上以高密度两行信息呈现；列表按钮文本不换行、不截断，窄宽度时工具栏横向滚动或收纳。
4. **请求详情区**：保留“概览 / 请求 / 响应 / 请求测试”四个标签；概览显示核心状态与异常摘要；请求和响应内容保持大阅读面积，复制、格式化与既有辅助处理能力不可丢失。
5. **请求测试区**：原有字段和能力全部保留，包括环境 Base、从抓包回填、方法/URL、SSL 证书校验、分类与接口库、Headers/Params/Body、导入导出、复制格式化、完整响应。编辑区和响应区改为纵向 `QSplitter`，默认响应区更大，允许用户上下拖动。
6. **环境管理**：主界面仅保留环境下拉与“环境配置”入口。新增、编辑、删除环境以及“保存为环境”移入环境配置弹窗；弹窗使用多环境列表管理。
7. **URL 过滤规则**：主界面不展示规则内容，仅保留“过滤配置”入口。弹窗中以多规则列表管理，支持新增、编辑、删除、启用/停用和排序；规则兼容现有 `url_filter_prefixes`，必要时仅升级为结构化配置。
8. **接口库与历史**：移除“加载所选”和“复制并重发”常驻按钮。接口库双击条目回填表单；历史 URL 通过右键菜单完成填充、复制完整 URL、复制 cURL、保存接口库和删除单条。历史清理由“历史清理配置”入口管理，显示影响范围与数量，并二次确认且默认焦点为取消。

### 安全与兼容边界

- 捕获请求、响应、Cookie、Token、密钥、明文等敏感会话信息只存在内存；不得写入 `data/interface_debug.json`、日志或任何新 JSON。
- 请求测试仍以用户保存的环境 Base（`scheme://host:port`）替换抓包 URL 的主机后发起；不改变网络边界、循环地址限制和 HTTPS 默认校验策略。
- 历史与接口库的现有持久化格式必须兼容；若新增 UI 偏好或规则结构，读取时兼容旧字段，写入时保留已知旧字段。
- 所有删除类操作遵循“取消在左、确认在右、默认焦点取消”；不自动执行破坏性操作。

## 文件影响总览

| 文件 | 处理 | 目的 |
|---|---|---|
| `panels/interface_debug_panel.py` | 主要修改 | 重排抓包区、左右工作台、详情页、请求测试布局；新增配置弹窗、右键菜单与分隔条状态保存。 |
| `tools/interface_debug_store.py` | 修改 | 兼容并规范化新增的 UI 偏好、垂直分隔条尺寸与可选的结构化 URL 过滤规则；继续禁止持久化报文。 |
| `tools/interface_session_view.py` | 视需要修改 | 仅在需要为两行会话摘要、异常标识或筛选计数抽纯函数时调整；不混入 PyQt 组件。 |
| `tools/iface_request_test.py` | 视需要修改 | 仅在结构化 URL 过滤规则需要纯函数转换时调整，保留 `strip_url_prefixes` 的旧列表兼容。 |
| `tests/test_interface_fiddler_workbench.py` | 主要修改 | 覆盖工作台结构、状态按钮、分栏偏好、详情标签和请求测试布局回归。 |
| `tests/test_interface_debug.py` | 修改 | 覆盖配置升级、敏感内容不落盘、环境/过滤规则兼容及请求测试安全回归。 |

不新增网络访问依赖；不改动抓包、代理恢复、CDP、IE 代理的底层实现，除非测试暴露与 UI 联动直接相关的缺陷。

## 分阶段实施任务

### Task 1: 建立配置兼容与纯函数测试基线

**Files:**
- Modify: `tests/test_interface_debug.py`
- Modify: `tests/test_interface_fiddler_workbench.py`
- Modify: `tools/interface_debug_store.py`
- Modify (only if needed): `tools/iface_request_test.py`

**Step 1: Write failing tests for UI preference normalization**

在 `tests/test_interface_debug.py` 增加以下断言：

```python
def test_ui_prefs_keep_vertical_splitter_and_never_store_payload(self):
    from tools.interface_debug_store import load_interface_debug_config, save_interface_debug_config
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'interface_debug.json')
        save_interface_debug_config({
            'ui_prefs': {
                'splitter_sizes': {'standard': [300, 700]},
                'request_test_splitter_sizes': [360, 640],
            },
            'url_filter_prefixes': ['/gateway'],
        }, path=path)
        cfg = load_interface_debug_config(path)
        self.assertEqual(cfg['ui_prefs']['request_test_splitter_sizes'], [360, 640])
        self.assertEqual(cfg['url_filter_prefixes'], ['/gateway'])
        raw = open(path, encoding='utf-8').read()
        self.assertNotIn('request_body', raw)
        self.assertNotIn('Authorization', raw)
```

**Step 2: Run the targeted tests and verify failure**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_debug.InterfaceDebugStoreTests -v
```

Expected: FAIL because `request_test_splitter_sizes` is not normalized or retained.

**Step 3: Implement minimal configuration normalization**

在 `tools/interface_debug_store.py` 的 `DEFAULT_UI_PREFS` 添加：

```python
'request_test_splitter_sizes': [360, 640],
```

在 `_normalize_ui_prefs()` 中验证该字段：仅接受至少两个整数，首项最小 `160`、次项最小 `220`；非法输入回退默认值。保留现有 `splitter_sizes` 字段逻辑，不改写旧配置中的其他数据。

若 URL 过滤规则本阶段维持 `list[str]`，不升级数据格式；由弹窗把多行规则转换为去重字符串列表即可。只有确认需要启用状态、优先级时，才新增 `url_filter_rules` 并保留 `url_filter_prefixes` 回退兼容。

**Step 4: Run targeted tests and verify pass**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_debug.InterfaceDebugStoreTests tests.test_interface_fiddler_workbench.SessionViewLogicTests -v
```

Expected: PASS；生成的配置文件不出现请求/响应正文或认证字段。

**Step 5: Commit the configuration baseline**

```bash
git add tools/interface_debug_store.py tests/test_interface_debug.py tests/test_interface_fiddler_workbench.py
git commit -m "test: cover interface workbench preferences"
```

### Task 2: 重构顶层工作台与抓包状态操作

**Files:**
- Modify: `panels/interface_debug_panel.py:229-460`
- Modify: `tests/test_interface_fiddler_workbench.py`

**Step 1: Write failing smoke tests for the confirmed control model**

在 `FiddlerPanelSmokeTests` 增加：

```python
def test_capture_control_is_one_stateful_action_and_keeps_proxy_tools(self):
    p = InterfaceDebugPanel('zh')
    self.assertTrue(hasattr(p, 'capture_toggle_btn'))
    self.assertFalse(p.test_listen_btn.isHidden())
    self.assertFalse(p.restore_proxy_btn.isHidden())
    self.assertTrue(p.capture_toggle_btn.text())
    self.assertFalse(hasattr(p, 'connect_btn') and p.connect_btn.isVisible())
```

**Step 2: Run the smoke test and verify failure**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_capture_control_is_one_stateful_action_and_keeps_proxy_tools -v
```

Expected: FAIL because the page currently exposes separate `connect_btn` and `stop_btn`.

**Step 3: Implement the minimal top-level layout change**

在 `_setup_ui()` 中：

1. 用 `capture_toggle_btn` 取代可见的开始/停止双按钮；根据当前监听状态调用现有 `_connect_or_start()` 或 `_stop_listen()`。
2. 保留旧 `connect_btn` / `stop_btn` 为隐藏兼容属性，或把既有状态更新逻辑抽为 `_refresh_capture_action()`，避免大量底层监听代码重写。
3. 让“测试连接”“恢复系统代理”和代理状态标签同一工具区展示；按钮设置 `setWordWrap(False)`，不使用固定过窄宽度。
4. 抓包控制区与搜索/筛选/导出/清空工具条保持视觉分层；筛选 Chip 使用可横向滚动容器，避免窗口变窄时文字换行。
5. 保留会话表 11 个字段、搜索、筛选、导出与清空的现有信号连接；不改变 `_rebuild_table()`、`_ingest_record()` 的数据行为。

**Step 4: Run layout smoke and existing session tests**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests tests.test_interface_fiddler_workbench.SessionViewLogicTests -v
```

Expected: PASS；开始/停止语义由同一主按钮切换，代理恢复与连接测试仍可用。

**Step 5: Commit the workbench shell**

```bash
git add panels/interface_debug_panel.py tests/test_interface_fiddler_workbench.py
git commit -m "feat: streamline interface capture workbench"
```

### Task 3: 扩展右侧详情为可读的概览、请求与响应区

**Files:**
- Modify: `panels/interface_debug_panel.py:454-539, 2077-2276`
- Modify: `tests/test_interface_fiddler_workbench.py`
- Modify (only if pure formatting extraction is needed): `tools/interface_session_view.py`

**Step 1: Write failing tests for stable detail tabs and response readability**

增加测试：

```python
def test_detail_workspace_keeps_overview_request_response_test_tabs(self):
    p = InterfaceDebugPanel('zh')
    self.assertEqual(
        [p.detail_tabs.tabText(i) for i in range(p.detail_tabs.count())],
        ['概览', '请求', '响应', '请求测试'],
    )
    self.assertGreaterEqual(p.resp_detail.minimumHeight(), 180)
    self.assertTrue(p.resp_detail.isReadOnly())
```

**Step 2: Run test and verify failure if labels or constraints drift**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_detail_workspace_keeps_overview_request_response_test_tabs -v
```

Expected: PASS or a narrow failure that guides only layout work. If it already passes, retain it as regression coverage before visual changes.

**Step 3: Implement the detail hierarchy without changing payload handling**

1. 在右侧详情上方增加固定摘要行：方法、完整 URL、状态、耗时、大小、时间、当前环境。摘要仅显示从当前选中记录派生的信息，使用既有脱敏策略。
2. 概览页按“关键指标 → 异常摘要 → 调用线索”组织；先用文本组件和现有 `_refresh_detail()` 数据填充，不新增诊断网络调用。
3. 请求页将 Headers / Params / Body 的阅读结果按区块输出；保持复制、格式化、网关辅助按钮及敏感展示确认逻辑。
4. 响应页默认放大正文阅读区，保留复制、格式化、网关辅助按钮；格式化失败时显示非阻塞错误提示，原正文不得丢失。
5. 不将 URL、请求头或响应正文写入任何配置字段。

**Step 4: Run focused regression suite**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests tests.test_interface_debug.BrowserDebugTests -v
```

Expected: PASS；脱敏显示与会话清空约束保持有效。

**Step 5: Commit the detail workspace changes**

```bash
git add panels/interface_debug_panel.py tests/test_interface_fiddler_workbench.py tools/interface_session_view.py
git commit -m "feat: improve interface detail readability"
```

### Task 4: 重构请求测试的纵向编辑/响应工作区

**Files:**
- Modify: `panels/interface_debug_panel.py:539-814`
- Modify: `tools/interface_debug_store.py`
- Modify: `tests/test_interface_fiddler_workbench.py`
- Modify: `tests/test_interface_debug.py`

**Step 1: Write failing tests for the vertical request-test splitter**

在面板测试中增加：

```python
def test_request_test_uses_resizable_editor_response_splitter(self):
    p = InterfaceDebugPanel('zh')
    self.assertTrue(hasattr(p, 'rt_editor_response_splitter'))
    self.assertEqual(p.rt_editor_response_splitter.orientation().name, 'Vertical')
    self.assertGreater(p.draft_preview.minimumHeight(), 120)
```

**Step 2: Run test and verify failure**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_request_test_uses_resizable_editor_response_splitter -v
```

Expected: FAIL because the current request editor and response preview share one vertical layout without a splitter.

**Step 3: Implement the request-test layout**

1. 环境行保留环境下拉与单个“环境配置”按钮；删除主页面的新增、编辑、删除环境按钮。
2. Base 行保留 Base 输入与“从抓包回填”；将“保存为环境”移动到环境配置弹窗的新增/编辑流程。
3. 用一个 `QSplitter(Qt.Vertical)` 包住“Headers / Params / Body 编辑器及请求工具条”和“响应元信息及完整响应 Body”；设置响应区更大的默认比例，并连接 `splitterMoved` 保存 `request_test_splitter_sizes`。
4. `Headers / Params / Body` 继续使用 Tab，移除 100px 的最大高度，让编辑区随拖拽获得实际可用空间。
5. 保留导入会话、导出明细、请求/响应复制和格式化的原始槽函数；工具条使用单行尺寸策略。
6. 方法、完整 URL、SSL 校验、分类、保存接口、分类管理、发送测试保持可见；发送测试为唯一 primary 按钮。

**Step 4: Run request-test and storage regressions**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_debug.InterfaceDebugStoreTests tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests -v
```

Expected: PASS；保存并重载后垂直分隔条尺寸保持，响应正文区域可扩展，未改变测试请求的 Base 重写安全策略。

**Step 5: Commit request-test layout**

```bash
git add panels/interface_debug_panel.py tools/interface_debug_store.py tests/test_interface_debug.py tests/test_interface_fiddler_workbench.py
git commit -m "feat: resize interface request test workspace"
```

### Task 5: 实现环境配置与 URL 过滤配置弹窗

**Files:**
- Modify: `panels/interface_debug_panel.py:565-618, 990-1031, 3174-3260`
- Modify: `tools/interface_debug_store.py`
- Modify: `tests/test_interface_debug.py`
- Modify: `tests/test_interface_fiddler_workbench.py`

**Step 1: Write failing tests for configuration dialog entry points**

```python
def test_request_test_has_environment_and_filter_config_entries(self):
    p = InterfaceDebugPanel('zh')
    self.assertTrue(hasattr(p, 'rt_environment_config_btn'))
    self.assertTrue(hasattr(p, 'rt_filter_config_btn'))
    self.assertFalse(hasattr(p, 'rt_url_filter_edit') and p.rt_url_filter_edit.isVisible())
```

**Step 2: Run test and verify failure**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_request_test_has_environment_and_filter_config_entries -v
```

Expected: FAIL because environments use three visible buttons and URL filters use a visible text field.

**Step 3: Implement focused dialogs**

1. 新增 `_show_environment_config_dialog()`：使用 `QDialog` + 环境列表，列出名称和 Base 地址；提供“新增环境”“编辑环境”“删除环境”。保存时验证 `validate_base_url()`，写入 `local_targets`、`default_target_id` 后重新调用 `_fill_local_targets()`。
2. 删除环境前调用统一确认弹窗，默认焦点在取消；删除当前默认环境时选择剩余首项或清空默认 ID。
3. 新增 `_show_url_filter_config_dialog()`：第一期采用列表/多行编辑管理多个 URL 前缀；提供新增、编辑、删除、上移、下移和保存。保存时去空、去重并写回 `url_filter_prefixes`，再同步已有 `strip_url_prefixes()`。
4. 主页面只保留“环境配置”“过滤配置”入口，隐藏旧 `add_target_btn`、`edit_target_btn`、`del_target_btn`、`rt_save_env_btn`、`rt_url_filter_edit`、`rt_url_filter_save_btn`，避免删除既有槽函数导致回归。
5. 若一期业务必须支持单条启用/停用和优先级，将新结构写入 `url_filter_rules`，并在读取时降级为启用规则的 `url_filter_prefixes`；否则不提前引入复杂数据模型。

**Step 4: Run configuration, URL rewrite and payload safety tests**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_debug.InterfaceDebugStoreTests tests.test_interface_debug.InterfaceDraftsTests tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests -v
```

Expected: PASS；多个环境可管理、过滤规则可保存并兼容旧数据，配置文件没有任何敏感报文。

**Step 5: Commit the configuration dialogs**

```bash
git add panels/interface_debug_panel.py tools/interface_debug_store.py tests/test_interface_debug.py tests/test_interface_fiddler_workbench.py
git commit -m "feat: consolidate interface test configuration"
```

### Task 6: 精简接口库/历史并迁移历史操作到右键菜单

**Files:**
- Modify: `panels/interface_debug_panel.py:662-722, 2693-3173`
- Modify: `tests/test_interface_fiddler_workbench.py`
- Modify (only if needed): existing interface-library persistence module imported by the panel

**Step 1: Write failing tests for list interaction policy**

```python
def test_library_history_actions_move_to_context_menu(self):
    p = InterfaceDebugPanel('zh')
    self.assertFalse(p.rt_lib_load_btn.isVisible())
    self.assertFalse(p.rt_lib_resend_btn.isVisible())
    self.assertTrue(p.rt_lib_list.contextMenuPolicy().name == 'CustomContextMenu')
```

**Step 2: Run test and verify failure**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_library_history_actions_move_to_context_menu -v
```

Expected: FAIL because the current panel exposes bottom-row load, resend, delete and clear buttons.

**Step 3: Implement mode-specific operations**

1. 移除可见的“加载所选”“复制并重发”按钮；接口库条目继续由双击或 Enter 回填请求表单，不自动发送。
2. 历史列表右键菜单提供：填充到请求 URL、复制完整 URL、复制为 cURL、保存到接口库、删除此条历史。删除单条前确认。
3. 接口库右键菜单保留适合库条目的加载、编辑/删除等既有能力，但不在历史模式误显示。
4. 在“历史”模式下，用单独的“历史清理配置”入口替代常驻清空按钮；弹窗支持“全部历史”“7 天前历史”“当前搜索结果”三种范围，先显示预计影响数量，再二次确认。
5. 历史清理只影响用户明确持久化的历史条目，不触碰当前内存抓包会话；完成后刷新列表与计数。

**Step 4: Run library/history regression tests**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests tests.test_interface_debug -v
```

Expected: PASS；双击仍可回填，历史右键可单条处理，清理流程需确认且当前抓包会话不被误清空。

**Step 5: Commit library/history changes**

```bash
git add panels/interface_debug_panel.py tests/test_interface_fiddler_workbench.py tests/test_interface_debug.py
git commit -m "feat: streamline interface library history actions"
```

### Task 7: 完成桌面自适应、主题与可访问性验收

**Files:**
- Modify: `panels/interface_debug_panel.py`
- Modify (only when token gaps are confirmed): `resources/style.qss`
- Modify: `tests/test_interface_fiddler_workbench.py`

**Step 1: Write failing narrow-layout regression test**

```python
def test_narrow_layout_can_hide_left_session_pane_without_hiding_capture(self):
    p = InterfaceDebugPanel('zh')
    p.apply_layout_mode('narrow', True)
    self.assertFalse(p.capture_toggle_btn.isHidden())
    self.assertFalse(p._toggle_list_btn.isHidden())
    p._toggle_session_list()
    self.assertTrue(p._session_list_widget.isHidden())
```

**Step 2: Run test and verify status**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_fiddler_workbench.FiddlerPanelSmokeTests.test_narrow_layout_can_hide_left_session_pane_without_hiding_capture -v
```

Expected: FAIL only if renamed control references or narrow layout behavior has drifted.

**Step 3: Implement responsive and accessibility refinements**

1. `>= 1280px`：左栏建议默认 32%–34%，右栏 66%–68%；用户拖拽优先并被记住。
2. `1024–1279px`：左栏最小约 280px；筛选和操作工具条可横向滚动，按钮不换行。
3. `< 1024px`：保留抓包和右侧详情；使用现有“隐藏/显示会话列表”控制左栏，不把 URL、响应、按钮文字压缩截断。
4. 深浅主题均使用现有 QSS 语义色；常规文字对比度达到 WCAG AA，状态不要仅依赖颜色表达。
5. 键盘可访问：Tab 顺序为抓包 → 搜索/筛选 → 会话表 → 详情 Tab → 请求测试；所有菜单项有明确文本；右键之外为常用回填保留双击/Enter。

**Step 4: Run focused tests and manual visual checklist**

Run:

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_debug tests.test_interface_fiddler_workbench -v
```

Then manually verify in `run.py`:

- 浅色和深色主题下控件、文字、状态色清晰；
- 1024px、1280px、宽屏下不出现按钮换行；
- 双分隔条可拖动、关闭重开后尺寸记忆；
- 无记录、抓包中、停止抓包、失败请求、长 JSON 响应、历史右键和配置弹窗均可正常工作。

**Step 5: Commit responsive polish**

```bash
git add panels/interface_debug_panel.py resources/style.qss tests/test_interface_fiddler_workbench.py
git commit -m "feat: refine responsive interface workbench"
```

## 回归测试与验收清单

### 自动化测试

最终至少执行：

```bash
"C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe" -m unittest tests.test_interface_debug tests.test_interface_fiddler_workbench -v
```

验收要求：

- 通过所有定向测试；
- 不将 `Authorization`、Cookie、Token、`request_body`、响应正文落入 `interface_debug.json`；
- 会话清空和应用退出仍会清理内存会话；
- 停止抓包不清空会话；
- 环境 Base 重写仍保留路径和 query；
- 默认 HTTPS 证书校验仍开启；
- 历史清理与单条删除均有确认且默认焦点为取消。

### 手工验收场景

1. 开始抓包后主按钮变为停止；停止后恢复开始，列表内容仍保留。
2. 输入 URL/Host/方法关键字并叠加 XHR、失败、慢请求筛选，列表与会话计数正确更新。
3. 选中一条请求，概览、请求、响应、请求测试四页都能看到匹配数据；敏感字段默认脱敏。
4. 请求测试中拖动上下分隔条，长 Headers、Params、Body 和长 JSON 响应均能获得足够空间。
5. 环境配置可新增、编辑、删除多个环境，切换环境仅重写测试 URL 主机部分。
6. URL 过滤配置不会占用主页面，可配置多条规则且旧 `url_filter_prefixes` 正常兼容。
7. 接口库双击回填但不自动发送；历史条目右键可复制/回填/保存/删除；历史清理范围与条数确认正确。
8. 1024px 以下可隐藏会话栏；所有按钮单行完整显示且高频抓包、测试连接、恢复系统代理始终可达。

## 提交与发布节奏

1. 每完成一个任务，在定向测试通过后按任务中的提交信息提交；不得提交 `data/`、安装包、临时截图或日志。
2. Task 7 完成后执行安全扫描与离线构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

3. 构建完成后核对：`dist/PengToolsHub.exe`、`Installer/`、`PengToolsHub_Offline_Setup.zip`，并记录 `resources/build_info.json` 的构建时间。
4. 最终执行 `git status`，确认仅提交源代码、测试、必要的 QSS 和本计划；再提交并推送 `origin main`。如用户明确要求暂不提交或仅进行设计验证，则不推送。
