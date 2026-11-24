# 产品定位、功能地图与行为边界

## 1. 软件是做什么的

PengToolsHub 是 Windows 上供开发与交付人员使用的本地工具台，当前版本显示为 V4 Private。它把需求管理、发版材料、SQL、接口文档、数据库操作、接口与日志排查、日常开发辅助、日报与学习、模型聊天和 Agent 工作放在同一个桌面入口中。

典型工作链是：记录需求与测试点 → 整理 SQL / 发版材料 → 更新接口文档 → 排查数据库、接口和日志 → 记录交付与日报。模块可以独立使用，不能为了视觉流程整齐而强制用户按这条链操作。

“本地/离线优先”是应用资源与用户数据边界，不表示所有功能不联网：用户配置的数据库、SSH、模型与接口请求仍按既有功能访问目标。不得把 UI 改造扩展成云平台、账号体系、团队权限、远程同步或新审批流。

用户提到的“需求 AI / 开发 AI”是仓库协作者角色；它们与软件里的“模型聊天 / Agent 工作台”是两个概念，不能混改。

## 2. 真实技术栈及是否需要新增技术

| 层 | 当前实现与权威文件 | 本轮允许的方向 |
|---|---|---|
| 桌面运行 | Python 3.12、PyQt6，`run.py`、`main_window.py` | 保留启动、单实例、托盘与退出链 |
| 轻量全局页面 | Vue 3、TypeScript、Vite，`frontend/src/` | 侧栏和首页组件、CSS、无业务副作用的动效 |
| Qt/Web 适配 | QWebEngine / QWebChannel，`ui/web_shell.py` | 复用已有 DTO、信号和回退；新增契约需单独明确范围 |
| 原生工作台 | `panels/` 与 `ui/` | 通过布局、QSS、绘制、代理与公共组件改造 |
| 主题 | `ui/theme_manager.py` | 单一 calm，Qt/Web 同语义；旧主题输入兼容归一 |
| 布局 | `ui/layout_metrics.py`、`ui/field_metrics.py` | 区分主窗宽和内容宽；沿既有密度与断点 |
| 导航 | `ui/navigation_model.py` | 统一提供侧栏、首页快捷工具和悬浮入口元数据 |
| 数据与逻辑 | `tools/`、`config.py` | 复用既有读取、保存、校验与任务接口 |
| 分发 | `scripts/build_release.ps1`、PyInstaller 资源布局 | 不因一次 UI 修复重做打包或升级路径 |

当前技术足以实现规格中的卡片、渐变、柔和阴影、线性图标、微动效、加载状态和响应式布局。Web 区域采用 CSS，原生区采用 QSS / QPainter / Qt 动画。原生“玻璃感”允许以半透明分层和实底降级实现，不承诺所有窗口跨平台实时模糊。

本轮不需要新增 UI 框架、图标在线服务、CDN、动效大依赖或窗口管理框架。若以后确需新增依赖，需求必须说明现有技术的具体缺口、离线资源与授权、包体和启动影响、维护者、验证及回退；不能仅因为某框架更熟悉就引入。

## 3. 导航与模块源码地图

导航有 22 个叶子入口，14、15 为父级，不是业务页面。以下面板文件均相对仓库根目录；最终实例创建以 `main_window.py` 为准，不按数组位置重编号。

| ID | 入口与用途 | 主要界面落点 | UI 改造必须保留 |
|---|---|---|---|
| 0 | 首页：工作概览（简洁/完整视图）、月度足迹、日报双向同步、快捷入口 | `frontend/src/dashboard/`；`panels/dashboard_panel.py` 原生保底 | 汇总口径、跳转 ID、示例隔离、日报联动与自然刷新 |
| 1 | 测试数据：合并「证件类型 + 车辆 VIN」全宽工作台 | `panels/credit_panel.py`（内嵌 `panels/vin_panel.py`） | 类型选项、证件与 VIN 生成/校验算法、复制 |
| 2 | 发版联动：需求、SQL 与材料 | `panels/sql_panel.py` | SQL 提取/生成、目录/SVN、导出与提签 |
| 3 | 接口文档更新 | `panels/docx_panel.py` | 模板、SQL 输入、字段映射和输出文件 |
| 4 | （兼容路由）原车辆 VIN，现并入 ID 1「测试数据」 | 路由归并至 `panels/credit_panel.py` | 兼容历史跳转与测试引用 |
| 5 | （已下线隐藏）原加解密模块 | 不再挂载于主导航与启动预热 | 保留兼容兜底提示，不主动暴露死入口 |
| 6 | 命令库：119+ 运维/JVM 诊断命令与 AI/终端联动 | `panels/ops_panel.py` | 搜索、生成、复制、安全提示与带入终端（绝不自动执行） |
| 7 | 设置 | `panels/settings_panel.py` | 单主题既定结果、其余设置键/默认值/保存/生效方式 |
| 8 | 自我学习 | `panels/personal_panel.py` | 资料管理、搜索与本地私有数据边界 |
| 9 | 日报 | `panels/personal_panel.py`，与学习复用栈实例 | 自动保存、提醒、按月树与首页实时双向联动 |
| 10 | 需求管理 | `panels/requirement_panel.py`、`panels/test_points_editor.py` | 筛选、状态、日期、测试点、附件、联动与保存 |
| 11 | 格式工具：JSON/XML/SQL/文本 + 图片/PDF ↔ Base64 离线互转 | `panels/format_panel.py`、`tools/media_base64.py` | 格式规则、错误定位、复制与纯本地 Base64 预览/保存 |
| 12 | 接口排查 | `panels/interface_debug_panel.py` | 抓取/请求测试、代理/证书/浏览器配置、队列和停止 |
| 13 | 日志排查 | `panels/ops_log_panel.py` | SSH 多机任务、独立底部命令坞、关键字截取、终端、AI 排查与导出 |
| 14 | 数据中心（统一工作区入口） | `panels/data_center_panel.py`、`ui/navigation_model.py` | 统一承载六类数据库工作区与兼容旧索引 18–23 |
| 15 | 模型父级（兼容索引，不在侧栏展开占位） | `ui/navigation_model.py` | 仅兼容路由 |
| 16 | 模型聊天 | `panels/model_chat_panel.py` | 模型配置、上下文、流式、停止和历史 |
| 17 | Agent 工作台 | `panels/agent_workbench_panel.py` | 项目目录绑定、执行模式、工具权限、停止与记录 |
| 18–21 | （兼容路由）Oracle / MySQL / OceanBase / 达梦 | `panels/ai_workbench_panel.py` 的 `AiWorkbenchPanel`，按 dialect 创建 | 方言、连接、事务、守卫、对象树、查询、结果和导出 |
| 22 | （兼容路由）Redis | `panels/db_redis_panel.py` | Key 类型、TTL、命令与修改确认 |
| 23 | （兼容路由）MongoDB | `panels/db_mongodb_panel.py` | 集合、文档、查询与 Shell 行为 |

注意：`ai_workbench_panel.py` 在当前装配中承担四类关系数据库工作台，不是根据文件名猜出的 Agent 页；导航注释中的概念类名也不等于实际存在的 Python 类。必须追踪实例创建。

## 4. 常见任务从哪里读代码

| 任务 | 界面之外继续追踪 |
|---|---|
| 首页任务数量、月份、进度 | `tools/dashboard_summary.py` 的 `build_dashboard_summary`、`monthly_release_tasks`；`tools/dashboard_release_items.py` |
| 需求保存与联动 | `tools/requirements.py`；MainWindow 连接的需求变更信号 |
| 发版、SQL、接口文档 | `tools/sql_tool.py`、`tools/release_prep.py`、`tools/docx_updater.py`、`tools/ticket_submit.py` |
| 数据库行为 | `tools/db_connect.py`、`tools/db_contracts.py`、`tools/sql_guard.py`、`tools/db_redis_ops.py`、`tools/db_mongo_ops.py` |
| 接口与日志 | `tools/http_capture.py`、`tools/capture_lifecycle.py`、`tools/iface_request_test.py`、`tools/ops_ssh.py`、`tools/ops_ssh_shell.py` |
| 模型和 Agent | `tools/intranet_llm.py`、`tools/model_chat_store.py`、`tools/agent_runtime.py`、`tools/agent_store.py` |
| 原生公共视觉 | `ui/page_chrome.py`、`ui/design_system.py`、`ui/dialog_buttons.py`、`ui/icons.py`、`resources/style.qss` |
| 引导、帮助与更新公告 | `ui/guided_tour.py`、`ui/help_dialog.py`、`ui/release_notes_dialog.py`、`tools/release_notes.py`、`resources/help/` |
| Loading 与动效 | `ui/aurora_progress.py`、`ui/thinking_indicator.py`、`ui/startup_splash.py`、`ui/motion.py`；以及使用它们的页面 |
| 悬浮窗 | `ui/quick_panel.py`、`ui/floating_shortcuts_editor.py`、导航模型 |
| 分栏持久化 | `ui/splitter_prefs.py` 的既有安装与恢复接口 |

## 5. 首页是一条怎样的数据链

业务数据由 Python 加载和汇总，`MainWindow._dashboard_summary_payload` 装配导航展示信息，`ui/web_shell.py` 负责桥接，Vue 读取 DTO 并渲染。原生 `DashboardPanel._apply_summary` 使用相同汇总能力。`tools/dashboard_summary.py` 不得反向导入 UI 来取得导航；所需展示数据由调用方注入。

本月任务依据现有 `actual_release_date` 及兼容日期字段和汇总实现，不另创“计划上线日期”口径。状态和测试点完成比例由既有规则计算；“动画看着更合理”不是更改完成条件的理由。需求在现有编辑器修改后，仍由原有变更信号驱动首页刷新。

删除首页旧卡片是展示调整，不能顺便删除历史需求、`recent` 兼容字段或其他页面的历史查看能力。示例数据只供无数据演示/原型，不得向保存接口发送虚假 ID，不得混入真实统计或导出。

## 6. 数据和生命周期底线

用户数据路径以 `config.local_data_dir()` 为准；开发态和打包态不能另造一套。不要把真实配置、连接密码、模型 Token、Cookie、抓取载荷写进需求文档、测试夹具、Git 或打包资源。

页面布局变化不应销毁再创建数据库连接、SSH 会话、模型流或编辑器内容。UI 动画必须随隐藏/销毁停止，业务任务是否继续由原逻辑决定。错误时保留原错误处理与恢复路径；不为了“整洁”吞掉异常提示。分栏偏好继续由既有 splitter 保存逻辑管理，不能靠 monkey patch `setSizes` 建立新的全局所有权。
