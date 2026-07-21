# PengTools V4 项目整体架构文档

> **版本**：V1.0 · 基于 V4.27 Private 实际代码编写
> **适用对象**：全体开发人员（新成员必读 / 老成员速查）
> **编写原则**：本文档描述的是**当前代码的真实架构**，以及为保持长期可维护性需要遵守的**架构边界与开发规范**。所有包名、文件名、类名、方法名均可在仓库中直接定位。
> **范围声明**：本文档只做现状整理与规范说明——**不引入新技术栈、不改变现有业务逻辑、不对现有代码结构做拆分/重构**。涉及的所有结构内容均与仓库当前状态一致。
> **更新日期**：2026-07-21

---

## 目录

1. [产品定位与架构目标](#一产品定位与架构目标)
2. [技术栈全景](#二技术栈全景)
3. [分层架构总览](#三分层架构总览)
4. [根目录文件职责](#四根目录文件职责)
5. [ui 包：基础 UI 能力层](#五ui-包基础-ui-能力层)
6. [tools 包：无界面业务逻辑层](#六tools-包无界面业务逻辑层)
7. [panels 包：界面模块层](#七panels-包界面模块层)
8. [main_window：装配与联动层](#八main_window装配与联动层)
9. [数据流与调用关系](#九数据流与调用关系)
10. [依赖方向规则（强制）](#十依赖方向规则强制)
11. [导航与 Stack 索引规范](#十一导航与-stack-索引规范)
12. [数据文件字典与升级兼容](#十二数据文件字典与升级兼容)
13. [线程与异步规范](#十三线程与异步规范)
14. [命名与代码规范](#十四命名与代码规范)
15. [测试架构](#十五测试架构)
16. [构建与发布架构](#十六构建与发布架构)
17. [安全边界架构](#十七安全边界架构)
18. [代码规模与结构现状](#十八代码规模与结构现状)

---

## 一、产品定位与架构目标

PengTools V4 是一个 **Windows 离线桌面工具台**（Python 3.12 + PyQt6），把日常工作中的需求管理、SQL 发版、接口文档、接口排查、加解密、日报、个人知识库等能力收敛在一个本地工具内。

架构设计服务于五个硬目标：

| 目标 | 架构上的体现 |
|---|---|
| **本地优先** | 唯一数据根 `config.local_data_dir()`，不依赖网络、账号、云服务 |
| **UI 与业务分离** | `panels/` 只做界面，`tools/` 只做逻辑，可独立测试 |
| **升级不伤数据** | 程序文件与用户 `data/` 物理隔离，JSON 读旧兼容 |
| **Private/标准隔离** | 单代码库双构建，Private 功能集中在一个导航模块 |
| **敏感数据零落盘** | 接口报文/密钥/Cookie 只存内存，安全边界写进架构 |

---

## 二、技术栈全景

### 2.1 核心技术

| 层级 | 技术 | 版本 | 用途 | 代码位置 |
|---|---|---|---|---|
| 语言 | Python | 3.12 | 全部 | 全仓库 |
| GUI 框架 | PyQt6 | ≥6.6 | 窗口、树、表格、线程、托盘 | `panels/` `ui/` |
| Qt 绑定 | PyQt6-Qt6 / sip | ≥6.6 / ≥13.6 | Qt 运行时 | — |

### 2.2 业务依赖（`requirements.txt`）

| 依赖 | 版本 | 用于哪个模块 | 哪个 tools 文件封装 |
|---|---|---|---|
| `python-docx` | ≥1.1 | 接口 DOCX 更新、学习模块 Word 读写 | `tools/docx_updater.py`、`tools/personal_knowledge.py` |
| `openpyxl` | ≥3.1 | 发版 Excel、学习模块 Excel | `tools/release_prep.py`、`tools/personal_knowledge.py` |
| `gmssl` | ==3.2.2 | 网关 SM2+SM4 国密解密 | `tools/gateway_crypto.py` |
| `msoffcrypto-tool` | ≥5.4 | 带密码 Excel 导入 | `tools/personal_knowledge.py` |
| `websocket-client` | ≥1.6 | Chromium CDP 接口排查 | `tools/browser_debug.py` |
| `mitmproxy` | ≥11 | 本机代理抓包（Private） | `tools/http_capture.py`、`tools/ie_proxy.py` |
| `pypinyin` | ≥0.51 | 拼音搜索 | `tools/pinyin_search.py` |

### 2.3 构建与系统依赖

| 工具 | 用途 |
|---|---|
| PyInstaller | `--onefile --windowed` 打包单 EXE |
| 本机 SVN 命令行 | `tools/svn_workspace.py` 经 `subprocess` 调用（需 TortoiseSVN command line tools） |
| PowerShell | 构建脚本 `scripts/build_release.ps1` / `scripts/build_private_release.ps1` |

### 2.4 关键技术约束（写进架构的硬规则）

- 运行代码**不引入** HTTP/WebSocket 服务端、浏览器内核、在线 CDN。
- **唯一例外**：Private 接口排查允许 `127.0.0.1` 的 CDP 与 mitmproxy，禁止绑定 `0.0.0.0`/局域网/公网。
- `pypinyin` 为**可选增强**：未安装时 `pinyin_search.py` 降级到内置首字母表，不得阻塞原文搜索。

---

## 三、分层架构总览

```
┌─────────────────────────────────────────────────────────┐
│  run.py            入口层：QApplication、高DPI、QSS、单实例   │
├─────────────────────────────────────────────────────────┤
│  main_window.py    装配层：导航、Stack、跨模块信号联动、托盘    │
├─────────────────────────────────────────────────────────┤
│  panels/           界面层：12 个业务面板（QWidget/QDialog）   │
│   ├─ dashboard / credit / sql / docx / vin / gateway     │
│   ├─ ops / settings / personal / requirement             │
│   └─ format / interface_debug                            │
├──────────────────────┬──────────────────────────────────┤
│  ui/                 │  tools/                           │
│  基础 UI 能力层        │  无界面业务逻辑层（纯 Python）        │
│  主题/响应式/对话框/   │  需求/SVN/发版/SQL/加密/抓包/        │
│  图标/托盘/单实例      │  拼音/JSON/XML/运维命令...          │
├──────────────────────┴──────────────────────────────────┤
│  config.py         配置层：数据目录、系统配置、读写兼容        │
├─────────────────────────────────────────────────────────┤
│  data/             数据层：用户 JSON（升级保留，不进安装包）   │
│  resources/        资源层：QSS/图标/模板/种子（打进安装包）    │
└─────────────────────────────────────────────────────────┘
```

**一句话记忆**：`panels 管界面，tools 管逻辑，ui 管公共界面件，config 管数据在哪，main_window 负责把它们连起来。`

---

## 四、根目录文件职责

| 文件/目录 | 职责 | 关键成员 |
|---|---|---|
| `run.py` | 程序入口 | `resource_path()`、`load_stylesheet()`、`_resolve_window_icon()`、`main()` |
| `main_window.py` | 主窗口装配 | `class MainWindow(QMainWindow)`，信号 `layout_mode_changed` |
| `config.py` | 配置中心 | 见 4.1 |
| `requirements.txt` | 依赖清单 | — |
| `panels/` `tools/` `ui/` | 界面层 / 业务逻辑层 / 公共 UI 层 | 见第五～七节 |
| `resources/` | 打进安装包的静态资源 | QSS、图标、模板、种子 |
| `tests/` | 定向测试 | `test_*.py` |
| `scripts/` | 构建与开发工具（权威位置） | `build_private_release.ps1`、`build_release.ps1`、`*.spec`、`build_workbook_seed.py` |
| `docs/` | 架构 / 交接 / UI 需求 | 见 `docs/README.md` |
| `packaging/` | 安装布局说明 | 见 `packaging/README.md` |
| 根目录 `build_*.ps1` | 转发到 `scripts/` 的便捷入口 | — |

### 4.1 `config.py` 方法清单（配置层唯一入口）

| 方法 | 职责 |
|---|---|
| `local_data_dir(executable, frozen)` | **唯一数据根判定**：开发态 `项目/data/`，打包态 `<exe旁>/data/` |
| `app_version_text(with_date)` | 读取 `resources/build_info.json` 版本号 |
| `load_systems()` / `save_systems()` | 系统配置（`systems.json`），读时 `_normalize_system` 补默认值 |
| `load_settings()` / `save_settings()` | 设置（`settings.json`），读时 `normalize_settings` 限制范围 |
| `load_requirement_ui()` / `save_requirement_ui()` | 需求 UI 尺寸（`requirement_ui.json`：splitter、列宽） |
| `ensure_config_dir()` | 确保 `data/` 存在 |
| `DEFAULT_SETTINGS` / `DEFAULT_SYSTEMS` | 默认值常量 |

**规范**：任何模块要拿数据目录，**必须**走 `config.local_data_dir()`，禁止自己拼路径、禁止写 `_MEIPASS` 或用户主目录。

---

## 五、ui 包：基础 UI 能力层

`ui/` 提供**与业务无关**的公共界面能力，供 `panels/` 和 `main_window` 复用。**ui 包不得 import panels 或 tools。**

| 文件 | 类/方法 | 职责 | 使用方 |
|---|---|---|---|
| `ui/theme_manager.py` | `class ThemeManager`（单例 `.instance()`，`load_template()`） | 四套主题 token 管理、QSS 模板渲染 | `run.py`、各 panel |
| `ui/responsive.py` | `apply_splitter_orientation()`、`editor_min_height()`、`set_subtitle_visible()` | 响应式断点适配 | 各 panel |
| `ui/navigation_model.py` | `class LayoutModeController`（信号 `layout_mode_changed`）、`ActionDensity`、`LayoutMode` | 四档布局模式（Wide/Standard/Compact/Narrow）判定 | `main_window` |
| `ui/quick_panel.py` | `class QuickPanel`、`ResponsiveActionBar`、`ActionRole`、`_BarItem` | 悬浮工具栏、主次按钮收纳 | `main_window` |
| `ui/design_system.py` | `apply_button()`、`apply_surface()` | 统一按钮/卡片视觉 | 各 panel |
| `ui/field_metrics.py` | `size_line()`、`size_combo()`、`size_date()`、`size_compact_button()` | 控件统一尺寸 | 各 panel |
| `ui/page_chrome.py` | `make_page_header()` | 页头统一结构 | 各 panel |
| `ui/confirm_dialog.py` | `confirm_action()`、`show_info()`、`show_success()`、`show_warning()`、`ConfirmActionDialog` | **统一确认/提示框（删除二次确认、默认焦点取消）** | 所有 panel |
| `ui/aurora_progress.py` | `class AuroraProgress`（`place_overlay()`） | 不占布局的 Loading 浮层 | 各 panel |
| `ui/icons.py` | `apply_icon()`、品牌图标加载 | 图标着色与多尺寸 | 各 panel |
| `ui/tray_service.py` | `class TrayService` | 系统托盘与通知 | `main_window` |
| `ui/single_instance.py` | `class SingleInstanceGuard` | 单实例（`QLocalServer`/`QLocalSocket`） | `run.py` |
| `ui/hotkey_service.py` | `class HotkeyService`、`_NativeHotkeyFilter` | 全局热键 | `main_window` |
| `ui/keep_awake_service.py` | `class KeepAwakeService` | 保活 | `main_window` |
| `ui/selection_delegate.py` | `HighContrastSelectDelegate` | 选中行高对比 | 树/表格 |
| `ui/json_viewer.py` | `class JsonViewer` | JSON 树形查看控件 | format / interface_debug panel |
| `ui/xml_workspace.py` | `class XmlWorkspace` | XML 编辑工作区控件 | format panel |
| `ui/floating_shortcuts_editor.py` | `FloatingShortcutsEditor` | 悬浮快捷键编辑 | settings panel |
| `ui/layout_metrics.py` | 布局尺寸常量/函数 | 统一间距 | 各 panel |

### ui 包新增规则

新增一个公共界面件前，先自问：**"这个东西会被 ≥2 个 panel 用吗？"** 是 → 放 `ui/`；否 → 留在对应 panel 文件内（私有类用 `_` 前缀）。

---

## 六、tools 包：无界面业务逻辑层

`tools/` 是**纯 Python 业务逻辑**，不 import PyQt6 控件（最多用 `QObject`/`QThread`/`pyqtSignal` 做异步），不操作 QWidget。**所有 tools 函数都应能被 unittest 直接调用。**

按业务域分组：

### 6.1 需求与发版域

| 文件 | 关键方法 | 职责 |
|---|---|---|
| `tools/requirements.py` | `load_requirements()`、`save_requirements()`、`normalize_requirement()`、`flag_is_active()`、`active_flags()`、`flag_status_text()`、`daily_template()` | 需求/BUG 数据模型、完成标记、日报模板 |
| `tools/release_prep.py` | `rank_requirements()`、`release_row_from_requirement()`、`branch_name_from_svn()`、Sheet 复制/追加（`_dated_sheets()`、`_new_date_sheet()`、`_copy_row_style()`）、常量 `RELEASE_HEADERS` | 发版 Excel 规则（23 列表头强约束） |
| `tools/svn_workspace.py` | `svn_binary()`、`run_svn()`、`validate_svn_url()`、`working_copy_info()`、`svn_status()`、`infer_online_month()`、锁/提交 | SVN 命令行封装，`class SvnError` |
| `tools/sql_tool.py` | `split_statements()`、`deduplicate_sql_statements()`、`is_ddl()`、`is_dml()`、`classify_sql_type()`、`strip_comments()` | SQL 解析与分类 |
| `tools/pinyin_search.py` | `normalize_query()`、`pinyin_full()`、`pinyin_initials()`、`build_search_blob()`、`match_query()`、`filter_by_query()` | 拼音搜索（可选 pypinyin + 降级表） |

### 6.2 文档与知识域

| 文件 | 关键方法 | 职责 |
|---|---|---|
| `tools/docx_updater.py` | `parse_column_defs()`、`split_sql_statements()`、`deduplicate_sql()`、`filter_docx_structure_sql()`、`detect_encoding()` | SQL 结构 → DOCX 更新 |
| `tools/docx_template_registry.py` | `match_document_template()`、`supported_system_names()` | DOCX 模板匹配 |
| `tools/personal_knowledge.py` | `extract_document_entries()`、`extract_word_entry()`、`read_text_file()`、`file_type_for_path()` | 学习资料提取（Excel/Word/TXT/MD/SQL/JSON） |
| `tools/daily_reports.py` | `load_reports()`、`save_reports()`、`is_reminder_due()`、`report_markdown()`、`normalize_reminder()` | 日报读写与提醒 |
| `tools/json_viewer.py` | `parse_json_text()`、`format_json_text()`、`iter_json_nodes()`、`search_json_nodes()` | JSON 解析（供 ui/json_viewer） |
| `tools/xml_formatter.py` | `format_xml_text()`、`normalize_xml_input()` | XML 格式化 |
| `tools/text_dev_helpers.py` | `encode_base64()`/`decode_base64()`、URL/Unicode 编解码、`parse_timestamp_input()` | 开发文本工具，`TextHelperError` |

### 6.3 接口排查域（Private）

| 文件 | 关键方法 | 职责 |
|---|---|---|
| `tools/browser_debug.py` | `discover_browsers()`、`launch_debug_browser()`、`build_launch_args()`、`is_loopback_host()`、`profile_dir()` | Chromium CDP，`BrowserDebugError` |
| `tools/ie_proxy.py` | `read_proxy_settings()`、`write_proxy_settings()`、`backup_proxy_to_config()`、`restore_proxy_from_snapshot()`、`apply_local_proxy()`、`mitm_ca_cert_path()` | WinINet 代理备份/恢复/证书 |
| `tools/http_capture.py` | `class HttpCaptureWorker`、`class _UrlCaptureAddon`、`flow_to_record()`、`flow_to_url_record()` | mitmproxy 抓包 worker，`HttpCaptureError` |
| `tools/interface_debug_store.py` | `load_interface_debug_config()`、`save_interface_debug_config()`、`normalize_interface_debug_config()`、`update_ui_prefs()` | 接口排查配置（**严禁存报文**） |
| `tools/interface_session_view.py` | `content_kind()`、`format_size()`、`host_of()`、`url_path_display()`、`normalize_column_key()` | 会话记录展示模型 |
| `tools/interface_drafts.py` | `validate_base_url()`、`rewrite_url()`、`build_postman_collection()`、`build_curl()`、`drafts_as_json_text()` | Postman/cURL 草稿（**只生成不发送**） |

### 6.4 其他工具域

| 文件 | 关键方法 | 职责 |
|---|---|---|
| `tools/gateway_crypto.py` | `decrypt_gateway_payload()` | SM2+SM4 网关解密（密钥只进内存） |
| `tools/ops_commands.py` | `search_commands()`、`build_command()`、`command_text()`、`contains_forbidden_delete()` | 运维命令库（不自动执行破坏性命令） |
| `tools/credit_code.py` | `generate_code()`、`validate_code()`、`generate_batch()`、`generate_company_name()` | 统一社会信用代码 |
| `tools/id_documents.py` | `generate_resident_id()`、`validate_resident_id()`、`generate_passport()` | 证件号码 |
| `tools/vin_generator.py` | `generate_vin()`、`validate_vin()`、`generate_vin_batch()` | VIN 生成 |
| `tools/china_regions.py` | `all_district_codes()` | 行政区划 |
| `scripts/build_workbook_seed.py` | `main()` | 学习种子构建（开发工具，非运行时依赖） |

### tools 包新增规则

1. **一个文件一个业务域**，不要把多个不相关的业务塞进一个文件。
2. **错误用自定义异常**：`XxxError(ValueError)` 或 `(RuntimeError)`，panel 捕获后转成用户可读提示。
3. **数据读写方法成对出现**：`load_xxx()` / `save_xxx()`，读时 `setdefault`/normalize 兼容旧数据。
4. **禁止 import panels/ui**；需要异步时把 `QThread` worker 放这里可以，但 worker 只能通过 signal 回主线程。

---

## 七、panels 包：界面模块层

`panels/` 是业务界面，每个文件对应一个导航模块。**panel 负责：组装控件、绑定事件、调用 tools、发信号。panel 不写业务规则（规则在 tools）。**

| 文件 | 主类 | 导航 index | 内部辅助类 | 主要调用的 tools |
|---|---|---|---|---|
| `panels/dashboard_panel.py` | `DashboardPanel` | 0 | `TaskRow` | （跳转信号） |
| `panels/credit_panel.py` | `CreditCodePanel` | 1 | — | `credit_code`、`id_documents` |
| `panels/sql_panel.py` | `SqlToolPanel` | 2 | `SqlExportWorker(QThread)` | `sql_tool`、`release_prep` |
| `panels/docx_panel.py` | `DocxUpdatePanel` | 3 | `DocxUpdateWorker(QThread)` | `docx_updater`、`docx_template_registry` |
| `panels/vin_panel.py` | `VinPanel` | 4 | — | `vin_generator` |
| `panels/gateway_panel.py` | `GatewayDecodePanel` | 5 | — | `gateway_crypto` |
| `panels/ops_panel.py` | `OpsPanel` | 6 | `CustomCommandDialog` | `ops_commands` |
| `panels/settings_panel.py` | `SettingsPanel` | 7 | `ThemePreviewWidget`、`ThemeCard` | （走 `config.py`） |
| `panels/personal_panel.py` | `PersonalPanel`（内含 `KnowledgeTab`、`DailyReportTab`） | 8/9 | `PasteKnowledgeDialog`、`KnowledgeEditDialog` | `personal_knowledge`、`daily_reports` |
| `panels/requirement_panel.py` | `RequirementPanel` | 10 | `RequirementTree`、`SvnWorker(QThread)`、`RequirementDialog`、`MonthPickerDialog`、`DateInput`、`_WrapTextDelegate` 等 | `requirements`、`svn_workspace`、`personal_knowledge`、`pinyin_search` |
| `panels/format_panel.py` | `FormatToolsPanel` | 11 | `_SqlFormatTab`、`_TextDevHelpersTab` | `xml_formatter`、`json_viewer`、`text_dev_helpers`、`sql_tool` |
| `panels/interface_debug_panel.py` | `InterfaceDebugPanel` | 12 | `_LaunchBrowserWorker(QThread)`、`_FilterChip` | `browser_debug`、`ie_proxy`、`http_capture`、`interface_debug_store`、`interface_session_view`、`interface_drafts` |

### panel 内部结构约定（自上而下）

```python
class XxxPanel(QWidget):
    # 1. 对外信号（供 main_window 联动）
    task_completed = pyqtSignal(str)
    # 2. __init__：只存依赖、不调 heavy 逻辑
    # 3. _build_ui()：组装控件（顶部操作区 → 内容区 → 状态区）
    # 4. _connect_signals()：事件绑定
    # 5. 业务槽函数 _on_xxx()：调 tools，处理结果
    # 6. apply_layout_mode(mode, low_height)：响应式适配（必须实现）
    # 7. resizeEvent()：Loading 浮层重定位
```

**私有辅助类用 `_` 前缀**（如 `_WrapTextDelegate`、`_FilterChip`），表示"只服务本 panel，不许外部 import"。

---

## 八、main_window：装配与联动层

`main_window.py` 的 `MainWindow` 是唯一知道"所有 panel 存在"的地方，承担四种职责：

### 8.1 装配（Assembly）
- 实例化全部 panel，按固定顺序加入 `QStackedWidget`。
- 左侧导航按钮 → `_show_panel(index)` 切换 Stack 页。

### 8.2 跨模块联动（信号总线）

panel 之间**不允许直接互相 import**，一律通过 `pyqtSignal` 由 main_window 中转：

| 信号（发出方 → 接收槽） | 业务含义 |
|---|---|
| `dashboard_panel.open_xxx` → `_show_panel(n)` | 工作台跳转各模块 |
| `requirement_panel.send_to_sql` → `_receive_requirement_sql` | 需求 → 发版联动 |
| `requirement_panel.send_to_docx` → `_receive_requirement_docx` | 需求 → 接口文档 |
| `requirement_panel.add_to_daily` → `_add_requirement_to_daily` | 需求 → 日报 |
| `requirement_panel.open_release_prep` → `_open_release_prep` | 需求 → 升级准备 |
| `interface_debug_panel.open_gateway` → `_open_gateway_from_iface` | 接口报文 → 网关解密 |
| `interface_debug_panel.open_format_json/xml` → `_open_format_json/xml` | 报文 → 格式工具 |
| `gateway_panel.open_interface_debug` → `_show_panel(12)` | 解密 → 接口排查 |
| `personal_panel.reminder_due` → `_show_private_notification` | 日报提醒 → 托盘通知 |
| `settings_panel.settings_changed` → `_apply_settings` | 设置变更全局生效 |
| `sql_panel/docx_panel.task_completed` → `_record_success` | 任务完成记录 |

### 8.3 全局服务挂载
`TrayService`、`SingleInstanceGuard`、`HotkeyService`、`KeepAwakeService`、`QuickPanel` 都在 main_window 装配。

### 8.4 响应式广播
`layout_mode_changed` 信号 → `_broadcast_layout_mode()` → 每个 panel 的 `apply_layout_mode(mode, low_height)`。

**规范**：新增跨模块联动时，**在 panel 上定义信号，在 main_window 里 connect**，禁止 panel 持有别的 panel 引用。

---

## 九、数据流与调用关系

### 9.1 典型调用链（以"需求 → 发版 Excel"为例）

```
用户操作 RequirementPanel
  → tools/requirements.py  load_requirements()     读 data/requirements.json
  → RequirementPanel 发信号 open_release_prep
  → main_window._open_release_prep()               中转
  → SqlToolPanel 展示候选
  → tools/release_prep.py  rank_requirements()     排序
  → tools/release_prep.py  release_row_from_requirement()
  → 写入 resources/release_workbook_template.xlsx   生成发版 Excel
  → SqlExportWorker(QThread) 后台导出               signal 回主线程
  → AuroraProgress 结束 Loading
```

### 9.2 数据流向规则

- **读**：panel → `tools/xxx.load_yyy(path=config.local_data_dir()/...)` → 返回**已 normalize 的字典/列表**。
- **写**：panel 收集用户输入 → `tools/xxx.save_yyy(data)` → 保留未知旧字段写回 JSON。
- **内存态**：接口报文、密钥、Cookie 只存在 panel/worker 的成员变量，**永不调用 save**。

---

## 十、依赖方向规则（强制）

```
run.py ──┐
         ▼
main_window ──► panels ──► tools
              │        │
              ▼        ▼
              ui ◄─────┘（panels 也可用 ui）
              ▲
              └── panels
config ◄── 所有层（拿数据目录）
```

| 规则 | 说明 |
|---|---|
| ✅ 允许 | panels → tools、panels → ui、panels → config、main_window → panels/ui、tools → config |
| ❌ 禁止 | tools → panels、tools → ui、ui → panels、ui → tools、panels → panels |
| ❌ 禁止 | 任何层 → 直接拼 `data/` 路径（必须走 `config.local_data_dir()`） |

**判断口诀**：import 之前想清楚——"被 import 的那一层，知不知道对方存在？" tools 不知道任何 panel 存在，ui 不知道任何业务存在。

---

## 十一、导航与 Stack 索引规范

导航 index、Stack index、显示规则是**强约定**，改导航必须同步五处：菜单、Stack 装配、状态提示、语言文本、本表。

| 导航 index | Stack index | 模块 | Panel | 显示规则 |
|---|---|---|---|---|
| 0 | 0 | 工作台 | `DashboardPanel` | 常显 |
| 1 | 1 | 证件类型 | `CreditCodePanel` | 常显 |
| 2 | 2 | 发版联动 | `SqlToolPanel` | 常显 |
| 3 | 3 | 接口文档更新 | `DocxUpdatePanel` | 常显 |
| 4 | 4 | VIN | `VinPanel` | 常显 |
| 5 | 5 | 网关解密 | `GatewayDecodePanel` | 常显 |
| 6 | 6 | 运维助手 | `OpsPanel` | 常显 |
| 7 | — | 设置 | `SettingsPanel` | 常显（左下角） |
| 8 | 8 | 自我学习 | `PersonalPanel.open_learning()` | **彩蛋解锁后显示（唯一允许隐藏）** |
| 9 | 8 | 日报 | `PersonalPanel.open_daily_report()` | **常显，禁止隐藏** |
| 10 | 9 | 需求管理 | `RequirementPanel` | **常显，禁止隐藏** |
| 11 | 10 | 格式工具 | `FormatToolsPanel` | 常显 |
| 12 | 11 | 接口排查 | `InterfaceDebugPanel` | 常显（Private） |

> 导航 8/9 共用 Stack 8（PersonalPanel 内部 Tab 切换），这是历史设计，新增模块不要复用这种模式。

---

## 十二、数据文件字典与升级兼容

唯一数据根：`config.local_data_dir()`。所有 JSON 读写遵守：**读时 `setdefault`/normalize 兼容旧数据，写时保留未知旧字段。**

| 文件 | 读写入口 | 内容 | 兼容要求 |
|---|---|---|---|
| `settings.json` | `config.load_settings()` | 主题、字号、语言、悬浮栏、关闭行为 | `normalize_settings()` 限制范围补默认 |
| `systems.json` | `config.load_systems()` | 系统名、SQL 标题、SVN 目录、账号/环境、路径模板 | `_normalize_system()` 补字段 |
| `requirements.json` | `tools/requirements.py` | 需求/BUG 台账、日期、SVN、SQL、标记 | `normalize_requirement()` 逐条补默认 |
| `requirement_ui.json` | `config.load_requirement_ui()` | splitter 尺寸、列宽 | 只存正整数数组，非法回退默认 |
| `daily_reports.json` | `tools/daily_reports.py` | 日报内容 | 日期键 `yyyy-MM-dd` |
| `daily_report_settings.json` | `tools/daily_reports.py` | 提醒开关/时刻 | 默认每天最多提醒一次 |
| `private_knowledge.json` | `tools/personal_knowledge.py` | 学习资料（Excel 保留结构化行列） | 不允许只存展示文本 |
| `ops_custom_commands.json` | `tools/ops_commands.py` | 自定义运维命令 | 列表/对象结构向后兼容 |
| `interface_debug.json` | `tools/interface_debug_store.py` | 路径、端口、代理/证书指纹、UI 偏好 | **严禁报文/Cookie/密钥** |
| `svn_workspaces/` | `tools/svn_workspace.py` | SVN 工作副本 | 视为用户数据，升级不删 |

**删除类操作**：统一走 `ui/confirm_dialog.confirm_action()`，取消在左、确认删除在右、**默认焦点取消**。

---

## 十三、线程与异步规范

| 场景 | 规范 |
|---|---|
| 耗时操作（扫描/SVN/导出/抓包） | 用 `QThread` worker（如 `SvnWorker`、`SqlExportWorker`、`HttpCaptureWorker`） |
| 线程通信 | worker **只能通过 signal 或 `QTimer.singleShot` 回主线程**，禁止后台线程直接操作 QWidget |
| Loading | `AuroraProgress` 浮层不占布局；后台静默任务 `show_loading=False` |
| 三条路径 | 成功/失败/异常都必须结束 Loading、恢复 UI 可操作 |
| 退出清理 | 接口排查停止/切模式/退出必须 `clear_session()`；IE 代理必须恢复快照 |

---

## 十四、命名与代码规范

### 14.1 文件与包
- 文件名全小写 + 下划线：`xxx_yyy.py`。
- panel 文件以 `_panel.py` 结尾；tools 文件按业务域命名（不带 `_panel`）。

### 14.2 类
- 主类 PascalCase，与文件同名：`RequirementPanel`、`ThemeManager`。
- **panel 私有辅助类加 `_` 前缀**：`_WrapTextDelegate`、`_FilterChip`、`_BarItem`——明确表示"外部不许 import"。
- worker 类以 `Worker` 结尾并继承 `QThread`：`SvnWorker`、`SqlExportWorker`。

### 14.3 方法
- 公有方法：`verb_noun()` —— `load_requirements()`、`build_command()`。
- 私有方法/槽函数：`_` 前缀 —— `_build_ui()`、`_on_flag_chip_clicked()`、`_save_splitter_sizes()`。
- 数据读写成对：`load_xxx()` / `save_xxx()`；normalize 单独命名：`normalize_xxx()`。

### 14.4 常量与错误
- 常量全大写：`RELEASE_HEADERS`、`DEFAULT_SETTINGS`。
- 自定义异常以业务域命名：`SvnError`、`DraftError`、`HttpCaptureError`、`BrowserDebugError`、`TextHelperError`。

### 14.5 注释与文档字符串
- 公共方法写一句话 docstring（中文，说明"做什么"，必要时写"兼容规则"）。
- 兼容性 hack 必须注释原因，例如 `# 兼容旧数据：仅校验非法值，不覆盖用户自定义比例`。

---

## 十五、测试架构

### 15.1 目录与策略
- `tests/` 与业务文件平级，命名 `test_<域>.py`。
- 策略：**本轮改造模块定向测试优先，不要求全量回归**。
- 重点测 **tools 纯函数**（不依赖 QApplication），UI 只做冒烟与关键交互断言。

### 15.2 现有测试与覆盖域

| 测试文件 | 覆盖 |
|---|---|
| `test_core.py` | 基础工具函数 |
| `test_release_prep.py` / `test_release_ui.py` | 发版 Excel 规则、Sheet 追加/复制、表头断言 |
| `test_requirement_flags.py` / `test_requirement_splitter.py` | 需求标记、分栏持久化 |
| `test_filelib_pinyin_release.py` | 文件库 + 拼音搜索 + 发版命名 |
| `test_interface_debug.py` / `test_interface_fiddler_workbench.py` | 接口排查会话合并、过滤、模式 |
| `test_startup_and_crypto_params.py` | 单实例、图标、加解密参数区 |
| `test_svn_lock.py` | 本地 SVN 锁定/冲突 |
| `test_theme_format_tools.py` / `test_theme_responsive.py` | 主题、响应式 |
| `test_xml_formatter.py` | XML 格式化 |
| `test_lazy_ui_selfcheck.py` / `test_lazy_workflow_phase1.py` | UI 自检、工作流 |

### 15.3 运行方式

```powershell
# 定向（推荐，按本轮改动选）
python -m unittest tests.test_requirement_splitter -v
# 组合
python -m unittest tests.test_filelib_pinyin_release tests.test_release_ui -v
```

**规范**：涉及发版 Excel 必须断言：同日 Sheet 追加、缺 Sheet 复制、列宽/表头不变、计划日期是 Excel 日期类型而非字符串。

---

## 十六、构建与发布架构

### 16.1 双轨构建

| 脚本 | 产物 | 规则 |
|---|---|---|
| `scripts/build_release.ps1` | `PengToolsHub_Offline_Setup.zip`（标准包） | **常规维护禁止运行** |
| `scripts/build_private_release.ps1` | `PengToolsHub_Private_Offline_Setup.zip` | 日常唯一构建入口（根目录 `build_private_release.ps1` 为转发） |

### 16.2 Private 构建流水线（脚本自动完成）

```
记录标准包 SHA-256
 → 更新发版 Excel 模板资源
 → 清理 PrivateInstaller 旧程序文件
 → 删除 PrivateInstaller/data（防携带用户数据）
 → PyInstaller 构建 PengToolsHub.exe（打入 QSS/图标/种子/模板）
 → 压缩 ZIP
 → 再次校验标准包哈希（被改则报错）
```

### 16.3 版本信息
`resources/build_info.json`：`{"version": "4.27", "edition": "Private", "build_date": "..."}`，由构建脚本写入，`config.app_version_text()` 读取。

---

## 十七、安全边界架构

| 边界 | 规则 | 落点 |
|---|---|---|
| 网络 | 运行代码无 HTTP 服务端/浏览器内核/CDN；接口排查仅 `127.0.0.1` | `browser_debug.is_loopback_host()` |
| 敏感数据 | 报文/Cookie/Token/Key/明文只存内存；停止/切模式/退出 `clear_session()` | interface_debug panel、`gateway_crypto` |
| 配置 | `interface_debug.json` 只存路径/端口/指纹/UI 偏好 | `interface_debug_store` |
| 代理 | IE 代理启动前备份 WinINet，停止/失败/退出恢复；证书只删记录的指纹 | `ie_proxy.py` |
| 草稿 | Postman/cURL 只生成不发送 | `interface_drafts.py` |
| 运维 | 不自动执行破坏性命令，`contains_forbidden_delete()` 拦截 | `ops_commands.py` |
| 删除 | 统一 `confirm_action()`，默认焦点取消 | `ui/confirm_dialog.py` |
| 内网 | SVN/真实抓包必须用户现场验证，文档不得写"已验证" | 交付规范 |

---

## 十八、代码规模与结构现状

> 本节是对当前代码的**客观盘点**（数据来自实际扫描，2026-07-21），仅作结构说明，不涉及任何改动。阅读代码时可据此预估文件体量与内部构成。

### 18.1 整体规模

| 指标 | 数值 |
|---|---|
| Python 源文件 | 71 个 |
| 总行数 | 约 24,222 行 |
| panels/ | 12 个文件 |
| tools/ | 26 个文件 |
| ui/ | 19 个文件 |
| tests/ | 16 个测试文件 |

### 18.2 主要文件的体量与内部构成

| 文件 | 行数 | 内部构成（一个文件内包含的职责块） |
|---|---|---|
| `panels/requirement_panel.py` | 2929 | 需求树、文件库、SVN 操作、`SvnWorker`、`RequirementDialog`、`MonthPickerDialog`、`DateInput`、`_WrapTextDelegate` 等 |
| `panels/interface_debug_panel.py` | 2039 | 三种监听模式、状态机、会话列表、详情视图、`_LaunchBrowserWorker`、`_FilterChip` |
| `tools/docx_updater.py` | 1408 | SQL 解析（`parse_column_defs`、`split_sql_statements` 等）+ DOCX 结构更新 |
| `panels/sql_panel.py` | 1238 | 三个业务页：升级准备、SQL 整理、系统配置 + `SqlExportWorker` |
| `panels/personal_panel.py` | 1188 | `KnowledgeTab`（学习）+ `DailyReportTab`（日报）+ 两个对话框 |
| `panels/docx_panel.py` | 891 | DOCX 更新界面 + `DocxUpdateWorker` |
| `main_window.py` | 756 | 主窗口装配、导航、跨模块信号、托盘/关闭行为 |
| `tools/browser_debug.py` | 746 | 浏览器识别、启动、CDP 连接与事件 |

### 18.3 结构特点（现状描述）

- **单文件多职责块**：业务量大的模块（需求管理、接口排查、发版联动）目前集中在一个 panel 文件内，通过类内分区（`_build_ui` / 槽函数 / 内部辅助类）组织。阅读这类文件时建议先看类清单再定位方法。
- **tools 层职责单一**：多数 tools 文件聚焦一个业务域，方法签名干净、错误类型清晰（`SvnError`、`DraftError`、`HttpCaptureError` 等），是当前架构中最易测试的一层。
- **ui 层统一封装**：确认框、Loading、主题、响应式、图标均在 ui 层单点实现，各 panel 复用，未出现各模块自造一套的情况。
- **配置集中于 `config.py`**：数据目录判定与 JSON 读旧兼容处理集中，升级兼容由统一入口保障。

### 18.4 团队规范共识（沿用现状，不含结构改动）

1. **Code Review 关注点**：依赖方向（第十节）、数据兼容（第十二节）、线程回主线程（第十三节）。
2. **新模块开发顺序**：先写 tools 纯函数 + 单测 → 再写 panel 装配 → 最后在 main_window 接信号。
3. **提交前自查**：`git status` 不带 `data/`、不带临时文件、标准包哈希未变。

---

## 附录 A：快速定位索引

| 我要改… | 去看 |
|---|---|
| 启动/图标/高DPI/单实例 | `run.py`、`ui/single_instance.py`、`ui/icons.py` |
| 导航/联动/托盘/关闭 | `main_window.py`、`ui/tray_service.py` |
| 数据目录/设置/系统配置 | `config.py` |
| 主题/响应式/Loading/确认框 | `ui/theme_manager.py`、`ui/responsive.py`、`ui/aurora_progress.py`、`ui/confirm_dialog.py` |
| 需求树/文件库/SVN | `panels/requirement_panel.py` + `tools/requirements.py`、`tools/svn_workspace.py` |
| 发版 Excel | `tools/release_prep.py` + `panels/sql_panel.py` |
| 接口排查/抓包 | `panels/interface_debug_panel.py` + `tools/browser_debug.py`、`tools/http_capture.py`、`tools/ie_proxy.py` |
| 加解密 | `panels/gateway_panel.py` + `tools/gateway_crypto.py` |
| 学习/日报 | `panels/personal_panel.py` + `tools/personal_knowledge.py`、`tools/daily_reports.py` |

## 附录 B：术语表

| 术语 | 含义 |
|---|---|
| Panel | 一个导航模块的界面类 |
| tools | 无界面业务逻辑层 |
| Stack | `QStackedWidget` 页面容器，index 与导航强约定 |
| 发版联动 | 原"SQL 整理"，串联需求/SQL/系统/发版 Excel |
| normalize | 读 JSON 时补默认值、修正类型的兼容处理 |
| Private 版 | 含接口排查等私人功能的构建，与标准包隔离 |
| loopback | `127.0.0.1`，接口排查唯一允许绑定的地址 |
