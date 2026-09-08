# PengToolsHub 晴空棱镜 · 界面全量盘点与基线冻结 (P00_surface_inventory)

- **工单编号**: `ROUND: PRISM-UI-P00-FIX-1`
- **基线提交 (Base SHA)**: `32d7880af3d7246d0e586e3d7b2e16854e712cce`
- **开发分支**: `ui/prism-v1`
- **设计权威文档**: `docs/ui/concepts-2026-09/prism-suite/UI优化需求_晴空棱镜_Agent实施规范.md`
- **视觉参考原型**: `docs/ui/concepts-2026-09/prism-suite/index.html`
- **静态代码基线**: `docs/ui/concepts-2026-09/prism-suite/implementation-source-baseline.json`
- **冻结日期**: 2026-09-08
- **本轮生产代码改动**: **0 (STRICTLY PROHIBITED)**

---

## 1. 导航结构盘点 (22 Leaf Pages + 2 Parent Folders)

依据 `ui/navigation_model.py` 及 `main_window.py` 运行时挂载机制权威定义，当前软件导航共有 **22 个业务叶子页面**，以及 **2 个纯折叠父级目录**（无独立 QWidget 页面，仅供侧栏折叠/展开）。

### 1.1 业务叶子页面清单 (22 / 22，全部源码路径与真实类名机械校验通过)

| 导航索引 (`nav_index`) | 中文名称 | 英文名称 | 业务图标角色 (`icon_role`) | 导航分组 (`group_key`) | 对应面板类 (`Panel Class`) | 真实源码文件路径 | 渲染通道 | 响应式支持 |
|:---:|:---|:---|:---|:---|:---|:---|:---:|:---:|
| **0** | 首页 | Home | `home` | `workspace` | `DashboardPanel` | `panels/dashboard_panel.py` | Web/原生双通道 | 是 (CSS Grid / QLayout) |
| **1** | 证件类型 | Documents | `document-id` | `devtools` | `CreditCodePanel` | `panels/credit_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **2** | 发版联动 | Release Link | `release` | `delivery` | `SqlToolPanel` | `panels/sql_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **3** | 接口文档更新 | Interface Docs | `doc-update` | `delivery` | `DocxUpdatePanel` | `panels/docx_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **4** | 车辆 VIN | Vehicle VIN | `vin` | `devtools` | `VinPanel` | `panels/vin_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **5** | 加解密 | Crypto | `shield-key` | `devtools` | `GatewayDecodePanel` | `panels/gateway_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **6** | 命令库 | Command Library | `operations` | `ops` | `OpsPanel` | `panels/ops_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **7** | 设置 | Settings | `settings` | (底部独立项) | `SettingsPanel` | `panels/settings_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **8** | 自我学习 | Learning | `learning` | `personal` | `PersonalPanel` (知识库Tab) | `panels/personal_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **9** | 日报 | Daily Report | `daily-report` | `delivery` | `PersonalPanel` (日报Tab) | `panels/personal_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **10** | 需求管理 | Requirements | `requirements` | `delivery` | `RequirementPanel` | `panels/requirement_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **11** | 格式工具 | Format Tools | `json` | `devtools` | `FormatToolsPanel` | `panels/format_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **12** | 接口排查 | API Debug | `api-debug` | `devtools` | `InterfaceDebugPanel` | `panels/interface_debug_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **13** | 日志排查 | Log Inspect | `search` | `ops` | `OpsLogPanel` | `panels/ops_log_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **16** | 聊天 | AI Chat | `chat` | `ai` | `ModelChatPanel` | `panels/model_chat_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **17** | 工作 | Agent Workbench | `chat` | `ai` | `AgentWorkbenchPanel` | `panels/agent_workbench_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **18** | Oracle | Oracle DB | `database` | `workspace` | `AiWorkbenchPanel` (`dialect='oracle'`) | `panels/ai_workbench_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **19** | MySQL | MySQL DB | `database` | `workspace` | `AiWorkbenchPanel` (`dialect='mysql'`) | `panels/ai_workbench_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **20** | OceanBase | OceanBase DB | `database` | `workspace` | `AiWorkbenchPanel` (`dialect='oceanbase'`) | `panels/ai_workbench_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **21** | 达梦 | Dameng DB | `database` | `workspace` | `AiWorkbenchPanel` (`dialect='dameng'`) | `panels/ai_workbench_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **22** | Redis | Redis DB | `database` | `workspace` | `RedisWorkbenchPanel` | `panels/db_redis_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |
| **23** | MongoDB | MongoDB DB | `database` | `workspace` | `MongoDBWorkbenchPanel` | `panels/db_mongodb_panel.py` | 原生 QWidget | 是 (`apply_layout_mode`) |

### 1.2 父级折叠目录 (2 / 2)

| 导航索引 (`nav_index`) | 中文名称 | 英文名称 | 业务图标角色 | 导航分组 | 交互行为 | 实施说明 |
|:---:|:---|:---|:---|:---|:---|:---|
| **14** | 数据中心 | Data Center | `database` | `workspace` | 仅折叠/展开子菜单 (18~23) | **无对应独立 QWidget 页面**，点击切换展开/折叠状态，不触发 `QStackedWidget.setCurrentIndex` |
| **15** | 模型 | AI | `chat` | `ai` | 仅折叠/展开子菜单 (16, 17) | **无对应独立 QWidget 页面**，点击切换展开/折叠状态，不触发 `QStackedWidget.setCurrentIndex` |

---

## 2. 弹窗与子窗口盘点 (26 QDialog Subclasses，全部实测导入通过)

经全代码库扫描，系统内共有 **26 个继承自 `QDialog` 的窗口与编辑器**，所有子窗口必须在晴空棱镜规范下进行视觉重构，同时保持模态行为、返回码、参数及业务方法不变。

| 序号 | 类名 (`Class Name`) | 真实源码定义位置 | 触发入口与所属模块 | 模态与交互类型 | 主要业务职责 |
|:---:|:---|:---|:---|:---|:---|
| 1 | `ObjectPickDialog` | `panels/ai_token_edit.py:244` | AI 规则与对象选择 | 模态 (`exec()`) | 数据库对象/表结构选择注入 prompt |
| 2 | `PasteKnowledgeDialog` | `panels/personal_panel.py:56` | 个人学习知识库 | 模态 (`exec()`) | 剪贴板大段文本快速解析并录入知识库 |
| 3 | `KnowledgeEditDialog` | `panels/personal_panel.py:88` | 个人学习知识库 | 模态 (`exec()`) | 编辑单条知识条目标题、分类、标签与正文 |
| 4 | `_SkillManagerDialog` | `panels/model_chat_panel.py:912` | 模型聊天面板 | 模态 (`exec()`) | 管理 AI Skills (新增/编辑/启用禁用/删除) |
| 5 | `CategoryManageDialog` | `panels/ops_log_panel.py:128` | 日志排查面板 | 模态 (`exec()`) | 服务器分组分类维护 (新增/重命名/删除分类) |
| 6 | `ServerEditorDialog` | `panels/ops_log_panel.py:258` | 日志排查面板 | 模态 (`exec()`) | 编辑 SSH 主机连接、凭据、服务日志路径列表 |
| 7 | `LogSettingsDialog` | `panels/ops_log_panel.py:636` | 日志排查面板 | 模态 (`exec()`) | 日志抓取与显示参数配置 (行数、编码、超时) |
| 8 | `ServerManageDialog` | `panels/ops_log_panel.py:693` | 日志排查面板 | 模态 (`exec()`) | 主机管理总览列表 (测试连通性/批量管理) |
| 9 | `CommandHistoryDialog` | `panels/ops_log_panel.py:885` | 日志排查面板 | 模态 (`exec()`) | SSH/Linux 历史命令回顾、快速填入与发送 |
| 10 | `CustomCommandDialog` | `panels/ops_panel.py:20` | 命令库面板 | 模态 (`exec()`) | 新增与修改自定义运维命令模版与变量 |
| 11 | `TicketSubmitConfigDialog` | `panels/ticket_submit_dialog.py:57` | 发版/需求提签 | 模态 (`exec()`) | 提签配置管理 (环境配置、模版参数、种子文件) |
| 12 | `TicketSubmitDialog` | `panels/ticket_submit_dialog.py:283` | 发版/需求提签 | 模态 (`exec()`) | 正式生成发版提签单，预览并执行提交操作 |
| 13 | `MonthPickerDialog` | `panels/requirement_panel.py:299` | 需求管理面板 | 模态 (`exec()`) | 实际上线月份年月拾取弹窗 (YYYY-MM) |
| 14 | `RequirementAttachmentDialog` | `panels/requirement_panel.py:454` | 需求管理面板 | 模态 (`exec()`) | 查看需求文档附件、Word/Excel导出与表格解析 |
| 15 | `SvnCheckoutDialog` | `panels/requirement_panel.py:643` | 需求管理面板 | 模态 (`exec()`) | SVN 检出目录配置与拉取确认 |
| 16 | `RequirementDialog` | `panels/requirement_panel.py:740` | 需求管理面板 | 模态 (`exec()`) | 核心需求新建/编辑弹窗 (含多系统绑定/测试点) |
| 17 | `TestPointsDialog` | `panels/test_points_editor.py:364` | 需求/测试点组件 | 模态 (`exec()`) | 需求测试点独立编辑窗口 (提取、勾选、排序) |
| 18 | `ImagePreviewDialog` | `ui/daily_rich_edit.py:25` | 日报富文本组件 | 模态 (`exec()`) | 日报中插入图片的双击大图无损预览 |
| 19 | `ConnectionDialog` | `ui/connection_dialog.py:40` | 6 大数据库面板 | 模态 (`exec()`) | 数据库连接配置 (Oracle/MySQL/OB/达梦/Redis/Mongo)|
| 20 | `FloatingShortcutsEditor` | `ui/floating_shortcuts_editor.py:23` | 设置/悬浮面板 | 模态 (`exec()`) | 悬浮窗 4~6 项快捷入口自定义勾选排布 |
| 21 | `UserGuideDialog` | `ui/help_dialog.py:32` | 帮助与关于入口 | 模态 (`exec()`) | 用户操作手册与全套快捷键说明展示 |
| 22 | `ConfirmActionDialog` | `ui/confirm_dialog.py:25` | 全局确认服务 | 模态 (`exec()`) | 危险操作确认框 (高亮危险/正常操作二次确认) |
| 23 | `CloseActionDialog` | `ui/confirm_dialog.py:153` | 主窗口关闭事件 | 模态 (`exec()`) | 窗口关闭时选择“最小化到托盘”或“退出程序” |
| 24 | `AppNoticeDialog` | `ui/confirm_dialog.py:265` | 全局通知服务 | 模态 (`exec()`) | 统一消息弹框 (成功、警告、错误通知提示) |
| 25 | `NextStepDialog` | `ui/confirm_dialog.py:352` | 流程引导服务 | 模态 (`exec()`) | 业务完成后指引下一步动作选择弹框 |
| 26 | `HttpsCertConsentDialog` | `ui/confirm_dialog.py:433` | 接口排查中心 | 模态 (`exec()`) | 根证书信任安装前风险告知与用户授权弹框 |

---

## 3. 渲染架构与边界定义

```text
                           ┌────────────────────────────────────────┐
                           │      MainWindow (QMainWindow)          │
                           │  - Keep Native Windows Titlebar Frame  │
                           │  - ContextHeader (h=52px, Read-only)   │
                           │  - QStatusBar (h=28px)                 │
                           └──────────────────┬─────────────────────┘
                                              │
                     ┌────────────────────────┴────────────────────────┐
                     ▼                                                 ▼
      ┌──────────────────────────────┐                  ┌──────────────────────────────┐
      │   Navigation Sidebar Host    │                  │      Content Stack Host      │
      ├──────────────────────────────┤                  ├──────────────────────────────┤
      │ [Preferred] Web Chrome View  │                  │ [Preferred 0] Web Dashboard  │
      │   (Vue 3 / Vite / TS)        │                  │   (Vue 3 / Vite / TS)        │
      │   - chrome.html              │                  │   - dashboard.html           │
      │   - QWebChannel bridge       │                  │   - QWebChannel bridge       │
      │                              │                  │                              │
      │ [Fallback] Native NavTree    │                  │ [Fallback 0] DashboardPanel  │
      │   - QTreeWidget / Paint      │                  │   - Native QWidget layout    │
      │   - 10s health timeout       │                  │                              │
      │   - offscreen / crash-safe   │                  │ [Pages 1..13, 16..23]        │
      │                              │                  │   - Dense Native QWidgets    │
      └──────────────────────────────┘                  └──────────────────────────────┘
```

### 3.1 Web 通道与原生保底通道
- **Web 通道**:
  - `frontend/src/chrome/`: 侧栏展示组件。
  - `frontend/src/dashboard/`: 首页综合看板。
  - 两者均通过 `ui/web_shell.py` 的 `QWebEngineView` 承载，严禁跨出自身矩形弹窗。
- **原生保底通道**:
  - `main_window.py` 内建 `NavigationTree`。
  - `panels/dashboard_panel.py` 内建原生 `DashboardPanel`。
  - 当检测到无 GPU、WebEngine 崩溃 (`renderProcessTerminated`) 或初始化超过 10 秒超时时，系统自动无缝切回原生保底，不影响业务操作。

### 3.2 悬浮窗 (`QuickPanel`) 规格
- **收起尺寸**: 严格保持 **52×52** 逻辑像素单图标模式。
- **展开模式**: 抽屉式侧向展开，包含 4~6 个快捷入口、模式切换、置顶状态指示。
- **锚点算法**: 保留既有多显示器边缘吸附、DPI 自适应及坐标持久化，禁止修改其几何计算引擎。

### 3.3 系统级边框与控制
- **非客户区边框**: 严格保留 Windows 原生系统边框与标题栏按钮（最小化、最大化/还原、关闭）。
- **禁止项**: 严禁引入任何第三方无边框窗口库（如 `qframelesswindow`、`pywin32`），严禁自绘最小化/最大化按钮，严禁破坏系统 Snap、多屏拖动与任务栏缩略图行为。

---

## 4. 基线截图归档矩阵 (Baseline Screenshot Matrix)

所有 P00 冻结截图已完整生成并归档于 `.codex_work/prism-p00/`（通过 `.gitignore` 保护，防止二进制污染 Git 仓库）。

### 4.1 主窗口全分辨率与双主题矩阵 (80 张)

涵盖 10 个代表性核心页面 × 2 种主题 (`calm` 晴空 / `black` 墨黑) × 4 种基准逻辑分辨率：
1. `1440x900` (Wide - 展开侧栏 248px)
2. `1280x800` (Standard - 展开侧栏 220px)
3. `1100x720` (Compact - 折叠侧栏 84px)
4. `960x640` (Narrow Low-height - 折叠侧栏 72px)

| 页面名称 | 页面标识 | Calm (晴空) 截图文件 | Black (墨黑) 截图文件 |
|:---|:---|:---|:---|
| 首页看板 | `home` | `main_home_calm_{W}x{H}.png` | `main_home_black_{W}x{H}.png` |
| 需求管理 | `requirements` | `main_requirements_calm_{W}x{H}.png` | `main_requirements_black_{W}x{H}.png` |
| Oracle 工作台 | `db_oracle` | `main_db_oracle_calm_{W}x{H}.png` | `main_db_oracle_black_{W}x{H}.png` |
| Redis 工作台 | `db_redis` | `main_db_redis_calm_{W}x{H}.png` | `main_db_redis_black_{W}x{H}.png` |
| 接口排查 | `api_debug` | `main_api_debug_calm_{W}x{H}.png` | `main_api_debug_black_{W}x{H}.png` |
| 日志排查 | `ops_log` | `main_ops_log_calm_{W}x{H}.png` | `main_ops_log_black_{W}x{H}.png` |
| 模型聊天 | `model_chat` | `main_model_chat_calm_{W}x{H}.png` | `main_model_chat_black_{W}x{H}.png` |
| 智能体工作 | `agent_workbench` | `main_agent_workbench_calm_{W}x{H}.png` | `main_agent_workbench_black_{W}x{H}.png` |
| 发版联动 | `release_link` | `main_release_link_calm_{W}x{H}.png` | `main_release_link_black_{W}x{H}.png` |
| 系统设置 | `settings` | `main_settings_calm_{W}x{H}.png` | `main_settings_black_{W}x{H}.png` |

### 4.2 核心子窗口与组件截图矩阵 (16 张)

| 窗口/组件名称 | 文件名 | 分辨率/尺寸 | 验证主题 |
|:---|:---|:---:|:---:|
| 需求详情编辑弹窗 | `dialog_requirement_{theme}.png` | 800×600 | Calm & Black |
| 数据库连接配置弹窗 | `dialog_connection_{theme}.png` | 600×500 | Calm & Black |
| 需求测试点编辑弹窗 | `dialog_test_points_{theme}.png` | 550×420 | Calm & Black |
| 危险操作二次确认弹窗 | `dialog_confirm_danger_{theme}.png` | 自适应 | Calm & Black |
| 窗口关闭行为询问弹窗 | `dialog_close_action_{theme}.png` | 自适应 | Calm & Black |
| 悬浮快捷入口配置弹窗 | `dialog_floating_shortcuts_{theme}.png` | 自适应 | Calm & Black |
| 悬浮窗收起状态 (52×52) | `quick_panel_collapsed_{theme}.png` | 52×52 | Calm & Black |
| 悬浮窗展开抽屉状态 | `quick_panel_expanded_{theme}.png` | 展开 | Calm & Black |

---

## 5. 结论与基线签署

1. **22 个功能叶子页面与 2 个折叠父级** 全部盘点无误，严格锁定导航映射规范，源码路径与真实类名全部经 Python AST 校验。
2. **26 个 QDialog 子窗口** 已全量建档并锁定生命周期与调用协议，全部实测可正常导入。
3. **96 张全矩阵截图** 已生成并存放于本地 `.codex_work/prism-p00/` 供后续各阶段像素级比对。
4. 本次交付物仅包含盘点文档与测试基准，**未对任何生产代码产生修改**。
