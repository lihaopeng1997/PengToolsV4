# Global UI Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立 PengToolsHub 全局主题、信息密度、页面骨架与导航视觉基础，同时不改变任一业务面板、导航到 Stack 的映射或安全行为。

**Architecture:** 第一阶段将主题 token 的校验和 QSS 渲染提取为可单测的纯函数；将 `ui_density` 与 `sidebar_collapsed` 加入现有设置兼容链路；新增仅承载视觉工具的 `ui/design_system.py` 与 `ui/page_chrome.py`。主窗口只消费这些公共能力，保留现有 Stack 装载顺序、nav index 映射和面板激活副作用。

**Tech Stack:** Python 3.12、PyQt6、Qt QSS、unittest、现有 ThemeManager 与配置 JSON。

---

## 不可变约束

1. 不改变 `main_window.py:124-131` 的 Stack 装载顺序：0–7 历史面板、8 Personal、9 Requirement、10 Format、11 Interface Debug、12 Ops Log。
2. 不改变 `main_window.py:_stack_index_for_nav()`：nav 8/9 → stack 8；10 → 9；11 → 10；12 → 11；13 → 12。禁止以侧栏数组位置推导 Stack index。
3. 不改变 `_show_panel()` 中接口排查离页暂停代理、进入/离开回调、设置页提醒同步、个人页内部切换及需求刷新等副作用。
4. 不更改业务面板中的抓包、SSH、日志、SQL、SVN、网关解密逻辑；不改变数据根目录或任何敏感数据落盘规则。
5. 保留现有 QSS token 替换、图标 URL 注入和主题监听器；新增 token 必须覆盖 `calm`、`clear`、`warm`、`night` 四套主题。

## 文件影响总览

| 路径 | 变更类型 | 目的 |
|---|---|---|
| `tests/test_theme_responsive.py` | 修改 | 为 token 完整性、QSS 渲染、密度/侧栏设置和导航映射增加回归断言。 |
| `config.py` | 修改 | 新增兼容设置字段 `ui_density`、`sidebar_collapsed`，在 `normalize_settings()` 中做回退与校验。 |
| `ui/theme_manager.py` | 修改 | 新增无 Qt 副作用的 token/QSS 校验函数，并让 `ThemeManager.render()` 调用它们。 |
| `ui/design_system.py` | 新建 | 集中定义密度常量、尺寸映射和设置解析，避免散落在主窗口与各面板。 |
| `ui/page_chrome.py` | 新建 | 提供可复用的页面标题/上下文/主操作条容器；不承载业务操作。 |
| `resources/style.qss` | 修改 | 增补全局 token、紧凑/舒适对象属性、按钮/状态/焦点/页面骨架样式。 |
| `main_window.py` | 修改 | 仅接入密度、侧栏折叠持久化、页面骨架和主题重注入后的视觉刷新；不改映射与业务副作用。 |
| `panels/settings_panel.py` | 修改 | 在“外观”分组增加信息密度与侧栏折叠偏好控件，沿用既有 `settings_changed` 保存链路。 |

## 分任务实施步骤

### Task 1: 为主题 token 与渲染缺口建立回归测试

**Files:**
- Modify: `tests/test_theme_responsive.py:37-124`
- Modify: `ui/theme_manager.py:1-403`

**Step 1: Write the failing test**

在 `NightThemeTokenTests` 中添加 required token 集，覆盖：`CONTROL_HEIGHT_COMPACT`、`CONTROL_HEIGHT_COMFORTABLE`、`ROW_HEIGHT_COMPACT`、`ROW_HEIGHT_COMFORTABLE`、`FOCUS_RING`、`STATUS_INFO_BG`、`STATUS_SUCCESS_BG`、`STATUS_WARNING_BG`、`STATUS_DANGER_BG`。添加两条断言：

```python
def test_all_themes_have_design_system_tokens(self):
    required = (
        'CONTROL_HEIGHT_COMPACT', 'CONTROL_HEIGHT_COMFORTABLE',
        'ROW_HEIGHT_COMPACT', 'ROW_HEIGHT_COMFORTABLE', 'FOCUS_RING',
        'STATUS_INFO_BG', 'STATUS_SUCCESS_BG', 'STATUS_WARNING_BG',
        'STATUS_DANGER_BG',
    )
    for theme_id, palette in THEMES.items():
        self.assertEqual(missing_theme_tokens(palette, required), (), theme_id)


def test_rendered_qss_has_no_unresolved_design_tokens(self):
    manager = ThemeManager.instance()
    manager.load_template()
    qss = manager.render('calm')
    self.assertEqual(unresolved_qss_tokens(qss), ())
```

**Step 2: Run test to verify it fails**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive.NightThemeTokenTests.test_all_themes_have_design_system_tokens tests.test_theme_responsive.NightThemeTokenTests.test_rendered_qss_has_no_unresolved_design_tokens -v
```

Expected: FAIL because helper functions and/or design-system palette tokens are absent.

**Step 3: Write minimal implementation**

在 `ui/theme_manager.py` 模块级新增纯函数：

```python
def missing_theme_tokens(palette: dict[str, str], required: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(key for key in required if not str(palette.get(key) or '').strip())


def unresolved_qss_tokens(qss: str) -> tuple[str, ...]:
    return tuple(sorted(set(re.findall(r'__[A-Z0-9_]+__', qss))))
```

为 `THEMES` 的每个 palette 填入同一组新增 token；在 `ThemeManager.render()` 完成 palette 与图标替换后，若仍有未替换 token 则抛出带 token 名称的 `RuntimeError`。不要改变已有 token 名称或图标替换顺序。

**Step 4: Run test to verify it passes**

重复 Step 2 命令。

Expected: PASS。

**Step 5: Commit**

```bash
git add tests/test_theme_responsive.py ui/theme_manager.py
git commit -m "test: cover global design tokens"
```

### Task 2: 为密度与侧栏偏好建立兼容设置链路

**Files:**
- Modify: `tests/test_theme_responsive.py:127-168`
- Modify: `config.py:62-85,161-203`
- Create: `ui/design_system.py`

**Step 1: Write the failing test**

新增 `DesignSystemSettingsTests`，覆盖旧配置回退、非法值修正和尺寸映射：

```python
def test_normalize_settings_defaults_global_density_preferences(self):
    settings = normalize_settings({'ui_density': 'unknown', 'sidebar_collapsed': 'yes'})
    self.assertEqual(settings['ui_density'], 'compact')
    self.assertTrue(settings['sidebar_collapsed'])


def test_density_metrics_are_stable(self):
    self.assertEqual(density_metrics('compact').control_height, 32)
    self.assertEqual(density_metrics('comfortable').control_height, 36)
    self.assertEqual(density_metrics('compact').row_height, 32)
    self.assertEqual(density_metrics('comfortable').row_height, 40)
```

**Step 2: Run test to verify it fails**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive.DesignSystemSettingsTests -v
```

Expected: FAIL because settings fields and `density_metrics()` do not yet exist.

**Step 3: Write minimal implementation**

1. 在 `config.DEFAULT_SETTINGS` 增加：

```python
'ui_density': 'compact',
'sidebar_collapsed': False,
```

2. 在 `normalize_settings()` 中规范化：

```python
density = str(result.get('ui_density') or 'compact').strip().lower()
result['ui_density'] = density if density in ('compact', 'comfortable') else 'compact'
result['sidebar_collapsed'] = bool(result.get('sidebar_collapsed', False))
```

3. 新建 `ui/design_system.py`：定义不可变 `DensityMetrics` dataclass、`DENSITY_METRICS` 和 `density_metrics(name)`。该模块不得 import 业务 panel，也不得读写配置文件。

**Step 4: Run test to verify it passes**

重复 Step 2 命令。

Expected: PASS，且旧 `settings.json` 缺少字段时仍返回默认值。

**Step 5: Commit**

```bash
git add config.py ui/design_system.py tests/test_theme_responsive.py
git commit -m "feat: add global density preferences"
```

### Task 3: 建立可复用页面骨架并测试无业务耦合

**Files:**
- Create: `ui/page_chrome.py`
- Modify: `tests/test_theme_responsive.py:170-212`

**Step 1: Write the failing test**

新增 `PageChromeTests`，验证页面骨架只生成视觉区块，不连接业务：

```python
def test_page_chrome_exposes_context_and_primary_action_slots(self):
    chrome = PageChrome('接口排查', '当前环境：模拟环境')
    self.assertEqual(chrome.title_label.text(), '接口排查')
    self.assertEqual(chrome.context_label.text(), '当前环境：模拟环境')
    self.assertIsNotNone(chrome.primary_actions)
    self.assertIsNotNone(chrome.secondary_actions)
```

**Step 2: Run test to verify it fails**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive.PageChromeTests -v
```

Expected: FAIL because `ui.page_chrome` does not exist.

**Step 3: Write minimal implementation**

新建 `ui/page_chrome.py`。提供 `PageChrome(QWidget)`：
- 标题 `QLabel` objectName 为 `page-title`；
- 上下文 `QLabel` objectName 为 `page-context`；
- `secondary_actions` 和 `primary_actions` 两个 `QHBoxLayout`；
- `add_secondary_action(widget)` 与 `add_primary_action(widget)`；
- 不保存业务状态、不发请求、不 import `panels` 或 `tools`。

**Step 4: Run test to verify it passes**

重复 Step 2 命令。

Expected: PASS。

**Step 5: Commit**

```bash
git add ui/page_chrome.py tests/test_theme_responsive.py
git commit -m "feat: add reusable page chrome"
```

### Task 4: 在 QSS 中实现全局 token、状态与焦点样式

**Files:**
- Modify: `resources/style.qss:1-220`
- Modify: `ui/theme_manager.py:1-403`
- Modify: `tests/test_theme_responsive.py:37-124`

**Step 1: Write the failing test**

增加 QSS 渲染断言，确认以下 objectName/属性选择器存在且没有 placeholder：

```python
def test_qss_contains_global_component_contract(self):
    manager = ThemeManager.instance()
    manager.load_template()
    qss = manager.render('night')
    for selector in ('#page-title', '#page-context', '#status-banner', '#primary-btn', '#danger-btn'):
        self.assertIn(selector, qss)
    self.assertIn('QPushButton:focus', qss)
    self.assertNotIn('__CONTROL_HEIGHT_COMPACT__', qss)
```

**Step 2: Run test to verify it fails**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive.NightThemeTokenTests.test_qss_contains_global_component_contract -v
```

Expected: FAIL until selectors and tokens are added.

**Step 3: Write minimal implementation**

在 `resources/style.qss`：
- 用 `__CONTROL_HEIGHT_COMPACT__` / `__CONTROL_HEIGHT_COMFORTABLE__` 定义 `QMainWindow[uiDensity="compact"]` 与 `QMainWindow[uiDensity="comfortable"]` 的控件和行高规则；
- 增加 `#page-title`、`#page-context`、`#status-banner`、`#danger-btn`；
- 为 `QPushButton:focus`、`QLineEdit:focus`、`QComboBox:focus`、`QTextEdit:focus` 增加 `__FOCUS_RING__` 边框；
- 用 `STATUS_*_BG` 和已有语义前景 token 定义四类状态标签；
- 不重写终端的 `TERM_*` token，不将终端色绑定回 `CODE_BG`。

在 `ui/theme_manager.py` 为每套主题提供相应 token 值；`night` 的 `FOCUS_RING` 必须在暗表面上可见。

**Step 4: Run test to verify it passes**

重复 Step 2 命令。

Expected: PASS。

**Step 5: Commit**

```bash
git add resources/style.qss ui/theme_manager.py tests/test_theme_responsive.py
git commit -m "feat: add global QSS component states"
```

### Task 5: 接入设置页的密度与侧栏偏好控件

**Files:**
- Modify: `panels/settings_panel.py:590-597` 及外观设置构建方法
- Modify: `tests/test_theme_responsive.py:170-212`

**Step 1: Write the failing test**

在 `PanelLayoutModeTests` 新增：

```python
def test_settings_exposes_density_preference(self):
    panel = SettingsPanel({**DEFAULT_SETTINGS, 'ui_density': 'comfortable'}, 'zh')
    self.assertEqual(panel.density_combo.currentData(), 'comfortable')
```

**Step 2: Run test to verify it fails**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive.PanelLayoutModeTests.test_settings_exposes_density_preference -v
```

Expected: FAIL because the density selector does not exist.

**Step 3: Write minimal implementation**

在设置页“外观”组增加：
- `QComboBox` objectName `density-combo`，选项“紧凑 / 舒适”（英文文案随 `set_language` 更新）；
- `QCheckBox` objectName `sidebar-collapsed-check`，文案“启动时收起侧栏”；
- 在现有设置收集/保存函数中写入 `ui_density` 与 `sidebar_collapsed`；
- 不新建第二个保存入口，继续由既有 `settings_changed` 驱动 `main_window._apply_settings()`。

**Step 4: Run test to verify it passes**

重复 Step 2 命令。

Expected: PASS。

**Step 5: Commit**

```bash
git add panels/settings_panel.py tests/test_theme_responsive.py
git commit -m "feat: expose global density settings"
```

### Task 6: 在主窗口接入视觉偏好但保持导航行为不变

**Files:**
- Modify: `main_window.py:173-246,496-569,662-705`
- Modify: `tests/test_theme_responsive.py:127-168`

**Step 1: Write the failing test**

新增纯静态回归测试，避免错误映射：

```python
def test_navigation_stack_mapping_is_unchanged(self):
    self.assertEqual(MainWindow._stack_index_for_nav(8), 8)
    self.assertEqual(MainWindow._stack_index_for_nav(9), 8)
    self.assertEqual(MainWindow._stack_index_for_nav(10), 9)
    self.assertEqual(MainWindow._stack_index_for_nav(11), 10)
    self.assertEqual(MainWindow._stack_index_for_nav(12), 11)
    self.assertEqual(MainWindow._stack_index_for_nav(13), 12)
```

新增 UI 断言：

```python
def test_apply_density_sets_window_property(self):
    window = MainWindow()
    window._apply_settings({**DEFAULT_SETTINGS, 'ui_density': 'comfortable'})
    self.assertEqual(window.property('uiDensity'), 'comfortable')
```

**Step 2: Run test to verify it fails**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive.MainWindowDesignSystemTests -v
```

Expected: FAIL because `uiDensity` is not yet applied.

**Step 3: Write minimal implementation**

在 `MainWindow`：
1. 新增私有 `apply_density_preferences(settings)`，读取 `density_metrics()` 后设置 `self.setProperty('uiDensity', density)` 与 `self._sidebar.setProperty('collapsed', collapsed)`；调用 `style().unpolish/polish` 或等效 Qt 刷新，避免重建 panel。
2. 在 `_apply_settings()` 完成主题注入后调用该方法，并将 `sidebar_collapsed` 应用于已有侧栏折叠控件/宽度逻辑；如果尚无可复用折叠方法，抽取现有侧栏折叠代码，不改 nav button 数组、对象名、index 或 `_show_panel()`。
3. 保持 `_stack_index_for_nav()` 原样。保留离开接口排查时 `on_panel_deactivated()` 与所有状态栏文案。

**Step 4: Run test to verify it passes**

重复 Step 2 命令，并运行现有映射覆盖：

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive -v
```

Expected: PASS；接口排查/日志/设置现有布局测试不回退。

**Step 5: Commit**

```bash
git add main_window.py tests/test_theme_responsive.py
git commit -m "feat: apply global layout preferences"
```

### Task 7: 第一阶段回归、离线构建与人工验收

**Files:**
- Modify: `docs/plans/2026-07-29-global-ui-foundation.md` only if validation reveals a required correction
- No source changes expected

**Step 1: Run targeted test suite**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_theme_responsive tests.test_core tests.test_ui tests.test_secure_store -v
```

Expected: PASS。若失败，先按系统化调试流程定位并补充最小修复测试，禁止跳过或删测试。

**Step 2: Perform manual offscreen smoke checks**

Run:

```bash
set QT_QPA_PLATFORM=offscreen && C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe run.py
```

Expected: 无 QSS unresolved token、无主题注入异常、无导航构建异常。若应用不适合自动退出，改为创建 `QApplication`/`MainWindow` 的短脚本检测，不启动阻塞事件循环。

**Step 3: Verify sensitive-data boundaries**

Run:

```bash
C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe scripts/scan_release_secrets.py
```

Expected: PASS，且本阶段不新增任何抓包正文、密钥、令牌或凭据持久化。

**Step 4: Build offline release package**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

Expected artifacts:
- `dist\PengToolsHub.exe`
- `Installer\PengToolsHub.exe`
- `PengToolsHub_Offline_Setup.zip`

同时读取 `resources/build_info.json` 记录构建时间。若打包失败，修复实际缺失依赖或 hidden import，不绕过安全扫描。

**Step 5: Commit and push**

```bash
git status
git add resources/style.qss ui/theme_manager.py ui/design_system.py ui/page_chrome.py config.py main_window.py panels/settings_panel.py tests/test_theme_responsive.py
git commit -m "feat: establish global interface foundation"
git push origin main
```

仅在定向测试、敏感扫描与离线构建全部成功后执行。不得加入 `data/`、构建日志、临时截图或安装包产物。

## 定向验证与发布验收

- 四个主题均能渲染，QSS 中不存在 `__[A-Z0-9_]+__` 未替换 token。
- 旧设置文件缺少新字段时无异常，默认得到 `ui_density="compact"`、`sidebar_collapsed=false`。
- 紧凑/舒适切换立即改变对象属性和 QSS 尺寸，切换主题后仍保留密度。
- nav → Stack 映射、接口排查离页暂停代理、个人页学习/日报双入口、需求/格式/接口/日志导航均不回退。
- 深色主题中终端色继续使用 `TERM_*` token；日志/终端正文不被全局密度挤压或隐藏。
- 设置、主题、密度、导航折叠能在重启后恢复；敏感数据未写入 `settings.json` 或其他新文件。
- 完整离线包构建成功，构建产物与 `resources/build_info.json` 时间一致。
