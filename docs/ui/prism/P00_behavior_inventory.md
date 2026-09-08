# PengToolsHub 晴空棱镜 · 交互行为盘点与回调冻结矩阵 (P00_behavior_inventory)

- **工单编号**: `ROUND: PRISM-UI-P00`
- **基线提交 (Base SHA)**: `32d7880af3d7246d0e586e3d7b2e16854e712cce`
- **开发分支**: `ui/prism-v1`
- **设计权威文档**: `docs/ui/concepts-2026-09/prism-suite/UI优化需求_晴空棱镜_Agent实施规范.md`
- **静态代码基线**: `docs/ui/concepts-2026-09/prism-suite/implementation-source-baseline.json`
- **冻结日期**: 2026-09-08
- **本轮生产代码改动**: **0 (STRICTLY PROHIBITED)**

---

## 1. 架构不可变契约与原型伪特性排除裁决

### 1.1 核心不可变行为原则
1. **纯展示层重构原则**: 仅重构组件布局、层次、边距、圆角、调色板、图标与微动效。严禁修改后端业务类、数据模型、线程调度、网络/数据库协议及数据持久化。
2. **信号与槽绑定不灭原则**: 所有现有控件的 `signal -> receiver` 映射必须 100% 保留。允许调整控件在布局中的位置或收入折叠区，但禁止改变其触发时机、参数签名及调用次数。
3. **状态与偏好持久化继承原则**: `splitter_prefs`、窗口大小记忆、悬浮窗坐标、主题语言设置等键值与存储格式严格保持不变。

### 1.2 原型演示特效与生产边界排除裁决 (PROTOTYPE EXCLUSIONS)

| 原型展示假象 | 生产正式裁决 | 实施约束与禁止项 |
|:---|:---|:---|
| **全局多标签 (Global Multi-Tab)** | **严格排除** | 当前 `MainWindow` 采用 `QStackedWidget` 承载，全局单页面切换。数据库工作台内部自带独立的 SQL Tab，但系统级不建立多标签生命周期。严禁引入全局 TabBar。 |
| **虚假通知中心 (Fake Notification Center)** | **严格排除** | 原型右上角的通知浮窗仅为前端假数据，生产无真实跨模块通知引擎。严禁在生产中新增无真实业务后端的假通知中心图标或浮层。 |
| **虚假全局搜索 (Fake Global Search)** | **严格排除** | 原型顶栏的全局搜索仅有前端静态过滤。生产只保留各模块内部真实的独立搜索过滤，顶栏 `ContextHeader` 仅展示当前模块名与快速面板触发器，不加入伪搜索。 |
| **自绘无边框与窗口控制按钮** | **严格排除** | 生产 `MainWindow` 采用 Windows 原生系统边框与标题栏。严禁自绘最小化/最大化/关闭按钮，避免破坏 Snap、DPI 缩放与多屏拖拽语义。 |
| **首页任务直接完成勾选** | **严格排除** | 原型点击代办直接更新为已完成。生产 `HomeBridge` 无写入权限，点击待办必须调用 `openRequirement(id)` 导航至原生需求编辑页进行正式状态流转。 |
| **模拟 AI 聊天与模拟导出成功** | **严格排除** | 严禁复制原型中的固定 9/12 进度、模拟流式输出或假 Toast 到生产。只允许对接真实 QThread Worker 与真实后端口径。 |

---

## 2. 核心业务领域行为冻结规范

### 2.1 需求与交付管理 (Requirement & Delivery)
- **实际上线日期与月份派生**:
  - 核心字段为 `actual_release_date` (格式: `YYYY-MM-DD`)。
  - `online_month` (格式: `YYYY-MM`) 必须且只能由 `actual_release_date[:7]` 自动派生，严禁在前端或数据层手工篡改或伪造独立字段。
- **测试点系统 (Test Points)**:
  - 需求测试点必须以标准 JSON 结构持久化保存于需求记录中。
  - 测试点勾选状态 (`done`) 变化必须实时同步并触发进度百分比重算。
  - 支持从需求描述 (`description`) 通过正则/规则一键提取测试点条目。
- **SVN 与文件库绑定**:
  - 需求支持关联本地目录或 SVN 检出目录。SVN 检出调用独立的 `SvnWorker` 线程，保持异步防阻塞。
- **发版联动 (Release Link)**:
  - 日期选择器联动发版清单刷新，提取所有选定日期的需求 SQL 并生成发版材料包。

### 2.2 数据库工作台 (Database Workbenches)
- **六大固定数据库面板**:
  - `Oracle` (18), `MySQL` (19), `OceanBase` (20), `Dameng` (21), `Redis` (22), `MongoDB` (23)。
  - 索引与方言映射锁定，不使用动态可插拔槽位。
- **连接管理与生命周期**:
  - 唤起 `ConnectionDialog`，支持保存、测试连通性、按方言加载驱动，密码使用本地加密存储。
- **SQL 执行与取消机制**:
  - 执行查询采用独占后台 QThread Worker，界面提供明确的执行与“取消 (`Cancel`)”按钮。
  - 分页查询与最大条数防护规则保持不变。
- **Splitter 布局持久化**:
  - 左侧对象树、右上编辑器、右下结果表格的 `QSplitter` 尺寸变动必须保存至 `splitter_prefs`，启动时自动恢复。
- **Redis 与 MongoDB 专项**:
  - Redis: 支持 DB 选择、Key 匹配扫描 (`SCAN`)、TTL 查看与修改、键值类型渲染。
  - MongoDB: 支持集合列表、JSON 查询过滤、排序与投影。

### 2.3 接口排查中心 (Interface Debug)
- **抓包引擎与代理切换**:
  - 启动/停止抓包切换系统代理设置，捕获流量进入独立环形缓冲区队列。
  - 证书安装引导弹出 `HttpsCertConsentDialog`，需用户二次授权。
- **实时摄入与防抖渲染**:
  - 捕获记录通过 `_sig_capture_record` 信号批量注入，前端表格使用 `_ingest_flush_timer` (100ms) 批量刷表，防止高频抓包卡顿 UI。
  - 文本过滤通过 `_search_timer` (300ms) 进行防抖。
- **单接口重放测试**:
  - 请求测试通过 `_RequestTestWorker` 独立异步线程发起，不阻塞主界面。

### 2.4 运维与日志排查 (Ops & SSH)
- **SSH 会话独立性**:
  - 每个连接 Tab 拥有完全独立的 Paramiko SSH 客户端、后台数据读取线程和终端写入器。
  - 关闭 Tab 时必须主动触发 `disconnect` 并清理 Worker 线程资源。
- **主机与分类管理**:
  - `ServerManageDialog` 与 `CategoryManageDialog` 提供完整 CRUD，操作后刷新主界面下拉树。
- **Linux 批量异步查询**:
  - 批量执行查询命令使用 `_LinuxQueryWorker`，支持并发执行与结果分发。

### 2.5 智能模型与 Agent 工作台 (AI & Agent)
- **流式输出与思考过程折叠**:
  - `_ChatWorker` 接收真实 SSE 流，前端实现增量 Markdown 追加。
  - 支持 `<think>...</think>` 思考标签的实时折叠展示，保持内容完整性。
- **Agent 执行模式与中断**:
  - 支持 Direct、ReAct、Plan 模式切换。
  - 提供即时“停止 (`Stop`)”按钮，发送中断信号终止 Agent 步进循环。

### 2.6 悬浮窗 (QuickPanel)
- **尺寸与贴边行为**:
  - 收起状态强制为 **52×52** 单图标形态。
  - 支持全屏幕边缘自动贴边吸附，记忆吸附屏幕与相对位置。
  - 展开为浮动快捷工具箱，点击空白处自动收起。
- **快捷入口编辑**:
  - `FloatingShortcutsEditor` 限制 4~6 个快捷入口配置，修改后即时刷新悬浮窗图标列表。

### 2.7 系统生命周期 (System Lifecycle)
- **单实例运行机制**:
  - 基于 `QLocalServer` / `QLocalSocket`，二次启动唤醒已有主窗口并激活到前台。
- **WebEngine 容灾与超时**:
  - 侧栏与首页加载设立 10 秒超时监控。超时或崩溃自动降级至原生 `NavigationTree` 与 `DashboardPanel`。
- **窗口关闭询问**:
  - 用户触发关闭窗口时，弹出 `CloseActionDialog`，支持保存“记住选择”到配置文件。

---

## 3. 回调行为冻结矩阵 (CALLBACK_BEHAVIOR_MATRIX)

本矩阵严格记录并冻结系统中所有核心可交互控件的信号连接关系。在晴空棱镜重构中，重写 UI 布局时必须严格保持原信号与原槽函数的连接。

| 模块 / 控件标识 (`Control ID`) | 原始信号 (`Signal`) | 原始接收者 (`Receiver`) | 启用条件 / 数据来源 | 参数签名 | 业务副作用与状态突变 | 允许的新布局位置 | 自动化验证测试 |
|:---|:---|:---|:---|:---|:---|:---|:---|
| **SqlToolPanel** / `self.release_date` | `dateChanged` | `_release_date_changed` | 始终启用 | `QDate` | 重新筛选发版需求，更新待打包候选列表 | 页面顶部工具条 | `test_release_ui.py` |
| **SqlToolPanel** / `self.refresh_release_btn` | `clicked` | `_load_release_candidates` | 始终启用 | 无 | 触发重新加载指定日期的候选需求 | 工具栏右侧操作区 | `test_release_ui.py` |
| **SqlToolPanel** / `self.release_generate` | `clicked` | `_generate_release_materials` | 存在已选需求 | 无 | 提取 SQL，生成发版脚本并打包输出 | 底部主操作条 | `test_release_ui.py` |
| **RequirementPanel** / `self.scan_btn` | `clicked` | `_scan_folder` | 始终启用 | 无 | 扫描本地目录或 SVN，导入需求条目 | 顶部操作栏 | `test_ui.py` |
| **RequirementPanel** / `self.update_all_btn` | `clicked` | `_update_all` | 始终启用 | 无 | 全量同步刷新所有需求记录 | 顶部操作栏 | `test_ui.py` |
| **RequirementPanel** / `self.status_filter` | `currentIndexChanged` | `_on_filter_changed` | 始终启用 | `int` | 过滤需求列表表格视图 | 过滤工具条 | `test_requirement_flags.py` |
| **RequirementDialog** / `source_btn` | `clicked` | `_load_documents` | 始终启用 | 无 | 弹出文件选择器，加载需求附件文档 | 对话框附件栏 | `test_ui.py` |
| **RequirementDialog** / `classify_btn` | `clicked` | `_classify` | 输入了标题/描述 | 无 | 调用规则或 AI 自动判断系统分类 | 标题输入框右侧 | `test_ui.py` |
| **RequirementDialog** / `description_edit` | `textChanged` | `_sync_test_points_description` | 始终启用 | 无 | 自动从描述文本提取更新测试点草稿 | 描述输入框下方 | `test_requirement_test_points.py` |
| **TestPointsEditor** / `add_btn` | `clicked` | `_add_point` | 输入框非空 | 无 | 新增一条需求测试点记录 | 测试点编辑区顶部 | `test_requirement_test_points.py` |
| **TestPointsEditor** / `extract_btn` | `clicked` | `_extract_from_description` | 描述包含条目 | 无 | 正则解析描述提取测试点并追加 | 测试点编辑区顶部 | `test_requirement_test_points.py` |
| **TestPointRow** / `self.check` | `toggled` | `_on_toggled` | 始终启用 | `bool` | 更新测试点完成状态，触发重新计分 | 单行测试点左侧 | `test_requirement_test_points.py` |
| **InterfaceDebugPanel** / `capture_toggle` | `toggled` | `_toggle_capture` | 始终启用 | `bool` | 启动/停止底层网络抓包服务及系统代理 | 顶部主操作栏 | `test_interface_debug_panel.py` |
| **InterfaceDebugPanel** / `search_edit` | `textChanged` | `_on_search_changed` | 始终启用 | `str` | 重置 300ms 防抖定时器，随后重建表格 | 过滤栏 | `test_interface_debug_panel.py` |
| **InterfaceDebugPanel** / `export_btn` | `clicked` | `_export_har` | 捕获列表非空 | 无 | 导出捕获记录为标准 HAR 文件 | 工具栏更多菜单 | `test_interface_debug_panel.py` |
| **OpsLogPanel** / `cmd_send_btn` | `clicked` | `_send_cmd_bar` | 已连接 SSH 会话 | 无 | 发送底部命令栏指令至当前主机终端 | 终端输入栏右侧 | `test_ops_log_panel.py` |
| **OpsLogPanel** / `server_manage_btn` | `clicked` | `_open_server_manager` | 始终启用 | 无 | 唤起 `ServerManageDialog` 进行主机管理 | 顶部操作栏 | `test_ops_log_panel.py` |
| **ServerEditorDialog** | `test_btn.clicked` | `_test` | IP/端口/凭据已填 | 无 | 启动 `_SshTestWorker` 异步验证主机连通性 | 弹窗底部操作栏 | `test_ops_log_panel.py` |
| **SqlWorkbench** / `run_btn` | `clicked` | `_execute_sql` | 处于已连接状态 | 无 | 启动异步查询 Worker 执行选中或全文 SQL | SQL 编辑器工具条 | `test_sql_workbench.py` |
| **SqlWorkbench** / `cancel_btn` | `clicked` | `_cancel_query` | 查询执行中 | 无 | 中断当前正在执行的数据库查询线程 | SQL 编辑器工具条 | `test_sql_workbench.py` |
| **SqlWorkbench** / `connect_btn` | `clicked` | `_open_connect_dialog` | 未连接或切换 | 无 | 弹出 `ConnectionDialog` 配置目标库参数 | 库对象树顶部 | `test_connection_dialog.py` |
| **ModelChatPanel** | `send_btn.clicked` | `_send_message` | 文本非空且空闲 | 无 | 启动 `_ChatWorker` 发起流式模型对话 | 输入框右下角 | `test_model_chat_panel.py` |
| **ModelChatPanel** | `stop_btn.clicked` | `_stop_generation` | 流式生成中 | 无 | 中断后台模型推理 QThread | 输入框右下角 | `test_model_chat_panel.py` |
| **ModelChatPanel** | `skill_btn.clicked` | `_open_skill_manager` | 始终启用 | 无 | 唤起 `_SkillManagerDialog` 编辑技能列表 | 工具栏 | `test_model_chat_panel.py` |
| **AgentWorkbench** | `execute_btn.clicked` | `_execute_task` | 输入了任务目标 | 无 | 按照选定模式启动 Agent 任务循环 | 任务输入区下方 | `test_agent_workbench.py` |
| **SettingsPanel** | `reset_layout_btn.clicked` | `_reset_layout_prefs` | 始终启用 | 无 | 清理 `splitter_prefs` 并恢复默认布局比例 | 设置页通用区域 | `test_settings_panel.py` |
| **SettingsPanel** | `edit_shortcuts_btn.clicked` | `edit_floating_shortcuts.emit`| 始终启用 | 无 | 发射信号唤起 `FloatingShortcutsEditor` | 设置页悬浮窗配置区 | `test_settings_panel.py` |
| **FloatingShortcutsEditor**| `buttons.accepted` | `_save_shortcuts` | 勾选 4~6 项 | 无 | 保存快捷配置并触发 `floating_shortcuts_changed` | 弹窗底部按钮栏 | `test_floating_shortcuts.py` |
| **MainWindow** | `closeEvent` | `_handle_close` | 触发窗口关闭 | `QCloseEvent` | 根据设置直接退出或弹出 `CloseActionDialog` | 主窗口系统框架 | `test_main_window.py` |

---

## 4. 交付与验证基线

1. **零生产侵入保证**: 本文档为纯规格与基线冻结交付物，严格遵循“功能逻辑与回调完全不变”的最高红线。
2. **自动化测试基准**:
   - Canonical 93 个测试模块全量自动化门禁执行通过 (`93 / 93 PASS, 0 FAIL, 0 CRASH, 0 TIMEOUT`)。
   - `git diff --check` 无空白或编码错误。
3. **后续阶段衔接**:
   - P01 将在此冻结基线基础上开展基础样式系统 (`theme_manager`、设计系统语义 token) 的升级，严格遵守本行为盘点所确立的界限。
