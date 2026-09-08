# PengToolsHub 晴空棱镜 · 交互行为盘点与回调冻结矩阵 (P00_behavior_inventory)

- **工单编号**: `ROUND: PRISM-UI-P00-FIX-1`
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
2. **信号与槽绑定不灭原则**: 所有现有控件的真实 `signal -> receiver` 映射必须 100% 保持连通。允许调整控件在布局中的容器位置或收入折叠区，但禁止改变其触发时机、参数签名及调用次数。
3. **状态与偏好持久化继承原则**: `splitter_prefs`、窗口大小记忆、悬浮窗坐标、主题语言设置等键值与存储格式严格保持不变。

### 1.2 原型演示特效与生产边界排除裁决 (PROTOTYPE EXCLUSIONS)

| 原型展示假象 | 生产正式裁决 | 实施约束与禁止项 |
|:---|:---|:---|
| **全局多标签 (Global Multi-Tab)** | **严格排除** | 当前 `MainWindow` 采用 `QStackedWidget` 承载，全局单页面切换。数据库工作台内部自带独立的 SQL Tab，但系统级不建立多标签生命周期。严禁引入全局 TabBar。 |
| **虚假通知中心 (Fake Notification Center)** | **严格排除** | 原型右上角的通知浮窗仅为前端假数据，生产无真实跨模块通知引擎。严禁在生产中新增无真实业务后端的假通知中心图标或浮层。 |
| **虚假全局搜索 (Fake Global Search)** | **严格排除** | 原型顶栏的全局搜索仅有前端静态过滤。生产只保留各模块内部真实的独立搜索过滤，顶栏 `ContextHeader` 仅展示当前模块名与快速面板触发器，不加入伪搜索。 |
| **自绘无边框与窗口控制按钮** | **严格排除** | 生产 `MainWindow` 采用 Windows 原生系统边框与标题栏。严禁自绘最小化/最大化/关闭按钮，避免破坏 Snap、DPI 缩放与多屏拖拽语义。 |
| **首页任务直接完成勾选** | **严格排除** | 原型点击代办直接更新为已完成。生产 `HomeBridge` 无写入权限，点击待办必须调用 `openRequirement(id)` 导航至原生需求编辑页进行正式状态流转。 |
| **模拟 AI 聊天流式输出** | **严格排除** | 源码 `_ChatWorker` 为同步获取后单次 `completed.emit(text)`。严禁在生产中伪造前端定时器逐字吐字的虚假 SSE 流。 |
| **模拟导出成功** | **严格排除** | 严禁复制原型中的固定 9/12 进度、模拟导出成功的假 Toast。只允许对接真实 QThread Worker 与真实后端口径。 |

---

## 2. 产品负责人批准的事后覆盖规范 (Product Owner Approved Override)

本节记录由产品负责人 (Product Owner) 显式批准的业务交互调整。**此项属于目标实施规范（在 P09B 落实），非原始 baseline 行为**，特此单列记录，防止在后续开发中被“基线完全冻结”条款过滤：

```text
APPROVED_POST_BASELINE_OVERRIDE:
DATA_CENTER_SQL_EXECUTION_SCOPE

CURRENT_BASELINE_BEHAVIOR:
selection exists
  -> selection
no selection
  -> statement_at_cursor(...)

PRISM_TARGET_BEHAVIOR:
selection exists
  -> execute selection only
no selection
  -> execute entire editor.toPlainText().strip()

EMPTY_EDITOR:
no execution + user feedback ("没有可执行的语句" / "Nothing to run")

VISIBLE_EXECUTE_BUTTON:
editor workspace only

TOP_RIGHT_EXECUTE_BUTTON:
REMOVE (从数据中心顶部标题栏移除冗余执行按钮，仅保留在编辑器工作区内部工具条)

CTRL_ENTER:
same execution-scope contract (遵循与执行按钮一致的选择区/全文执行逻辑)

F5:
same execution-scope contract (遵循与执行按钮一致的选择区/全文执行逻辑)

EXECUTOR / SQL_GUARD / CONFIRMATION / PAGING:
UNCHANGED (执行引擎、SQL守卫语法校验、危险确认弹框、分页限制等均保持不变)

IMPLEMENTATION_STAGE:
PRISM-UI-P09B (数据中心工作台重构工单)
```

---

## 3. 核心业务领域真实源码行为盘点

### 3.1 需求与交付管理 (Requirement & Delivery)
- **实际上线日期与月份派生**:
  - 核心字段为 `actual_release_date` (格式: `YYYY-MM-DD`)。
  - `online_month` (格式: `YYYY-MM`) 严格由 `actual_release_date[:7]` 自动派生，严禁在前端或数据层手工篡改或伪造独立字段。
- **测试点系统 (Test Points)**:
  - 需求测试点以标准 JSON 结构持久化保存于需求记录中。
  - 测试点勾选状态 (`done`) 变化必须实时同步并触发进度百分比重算。
  - 支持从需求说明 (`description`) 通过规则提取测试点条目。
- **搜索防抖机制**:
  - `RequirementPanel.search_edit.textChanged` 连接至 `self._req_search_timer.start()`。
  - `_req_search_timer` 为单次触发定时器，真实源码定时间隔为 **150ms**，超时后调用 `self._refresh()`。

### 3.2 数据库工作台 (Database Workbenches)
- **六大固定数据库面板**:
  - `Oracle` (18), `MySQL` (19), `OceanBase` (20), `Dameng` (21) 复用 `AiWorkbenchPanel`。
  - `Redis` (22) 使用 `RedisWorkbenchPanel`，`MongoDB` (23) 使用 `MongoDBWorkbenchPanel`。
- **连接管理**:
  - `self.conn_new_btn.clicked.connect(lambda: self._edit_connection(new=True))`。
  - `self.conn_edit_btn.clicked.connect(lambda: self._edit_connection(new=False))`。
  - 唤起 `ConnectionDialog`，支持保存、测试连通性、按方言加载驱动，密码使用本地安全加密存储。
- **SQL 执行与控制**:
  - `self.run_btn.clicked.connect(lambda: self._run_sql(reset=True))`。
  - 快捷键 `Ctrl+Return` 与 `F5` 均连接 `lambda: self._run_sql(reset=True)`。
  - 当前基线行为：编辑器有选中文本时执行选中 SQL；无选中时调用 `statement_at_cursor(editor.toPlainText(), pos)`。
  - `self.scan_cancel_btn.clicked.connect(self._cancel_scan)` 取消正在执行的库表元数据扫描。
  - `self.all_btn.clicked.connect(self._on_all_or_cancel)` 执行或取消当前结果全量抓取。
- **Splitter 布局持久化**:
  - 左侧对象树、右上编辑器、右下结果表格的 `QSplitter` 尺寸变动必须实时写入 `splitter_prefs`，启动时自动恢复。

### 3.3 接口排查中心 (Interface Debug)
- **抓包开关控件**:
  - 真实控件属性为 `self.capture_toggle_btn` (QPushButton)，其信号为 `clicked`，连接至 `self._toggle_capture`。
- **定时器与防抖数值 (严格基于源码实测)**:
  - 搜索过滤定时器 `self._search_timer`：`setSingleShot(True)`，定时间隔为 **150ms**，超时后连接 `self._rebuild_table`。由 `self.filter_edit.textChanged` 触发。
  - 抓包批量刷表定时器 `self._ingest_flush_timer`：`setSingleShot(True)`，定时间隔为 **220ms**，超时后连接 `self._flush_ingest_ui`。
- **单接口重放测试**:
  - 请求测试通过 `_RequestTestWorker` 独立异步线程发起，不阻塞主界面。

### 3.4 运维与日志排查 (Ops & SSH)
- **SSH 终端执行与主机管理**:
  - 底部命令发送按钮为 `self.cmd_send_btn.clicked.connect(self._send_cmd_bar)`。
  - 主机管理弹窗触发按钮为 `self.server_toggle_btn.clicked.connect(self._open_server_manage_dialog)`。
  - 每个终端 Tab 维护独立的 Paramiko SSHClient 及后台读取线程，Tab 关闭时主动断开连接并清理线程。

### 3.5 智能模型与 Agent 工作台 (AI & Agent)
- **ModelChatPanel 动作按钮机制**:
  - 界面仅有单一动作按钮 `self.send_btn`，其 `clicked` 信号连接至 `self._on_action_clicked`。
  - `_on_action_clicked()` 内部根据 `self._is_running` 状态智能分派：空闲时调用 `self._send()`（并将按钮文案更新为“停止”）；推理中调用 `self._stop()`（并将按钮文案恢复为“发送”）。
  - **不存在独立的 `stop_btn`**。
- **ModelChatPanel 后台 Worker 事实**:
  - 后台工作线程为 `_ChatWorker`，在 `run()` 方法中通过 `tools.intranet_llm.chat_completions(self.messages, cfg=self.cfg)` 同步获取结果后，单次发射 `self.completed.emit(text)`。当前基线**尚未实现流式 SSE 增量追加**。
- **AgentWorkbenchPanel 动作按钮机制**:
  - 同样采用单一操作按钮 `self.send_btn.clicked.connect(self._on_action_clicked)`，运行期间动态切换“发送”与“停止”，**不存在独立的 `execute_btn`**。

### 3.6 悬浮窗 (QuickPanel)
- **尺寸与贴边行为**:
  - 收起状态强制为 **52×52** 逻辑像素单图标形态。
  - 支持全屏幕边缘自动吸附与坐标持久化。
- **快捷入口编辑**:
  - `FloatingShortcutsEditor` 中的 `done_btn.clicked.connect(self._finish)`，校验并保存 4~6 个快捷入口至设置。

### 3.7 系统生命周期 (System Lifecycle)
- **WebEngine 容灾与超时**:
  - 侧栏与首页加载设立 10 秒超时监控。超时或崩溃自动降级至原生 `NavigationTree` 与 `DashboardPanel`。
- **窗口关闭询问**:
  - `MainWindow.closeEvent` 根据 `close_ask_each_time` 配置调用 `_ask_close_action()`，弹出 `CloseActionDialog`。

---

## 4. 回调行为冻结矩阵 (CALLBACK_BEHAVIOR_MATRIX)

本矩阵严格记录并冻结系统中所有核心可交互控件的信号连接关系，明确区分**当前基线行为**与**晴空棱镜规划行为**，验证测试全部关联已验证的真实测试文件。

| 模块 / 控件标识 (`Control ID`) | 原始信号 (`Signal`) | 原始接收者 (`Receiver`) | 启用条件 / 数据来源 | 参数签名 | 当前基线行为 (`Current Baseline Behavior`) | 晴空棱镜规划行为 (`Prism Target Behavior`) | 允许的新布局位置 | 真实自动化验证测试文件 (`Verified Test File`) |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| **SqlToolPanel** / `self.release_date` | `dateChanged` | `self._release_date_changed` | 始终启用 | `QDate` | 重新按日期筛选发版候选需求，更新列表 | 保持原有筛选与候选刷新逻辑 | 页面顶部工具条 | `tests/test_release_ui.py` |
| **SqlToolPanel** / `self.refresh_release_btn` | `clicked` | `self._load_release_candidates` | 始终启用 | 无 | 触发重新加载指定日期的候选需求 | 保持原有候选刷新逻辑 | 工具栏右侧操作区 | `tests/test_release_ui.py` |
| **SqlToolPanel** / `self.release_generate` | `clicked` | `self._generate_release_materials` | 存在已勾选需求 | 无 | 提取 SQL 并生成发版打包脚本及材料包 | 保持材料生成与压缩打包逻辑 | 底部主操作条 | `tests/test_release_ui.py` |
| **RequirementPanel** / `self.scan_btn` | `clicked` | `self._scan_folder` | 始终启用 | 无 | 扫描本地目录或 SVN，导入需求条目 | 保持本地与 SVN 扫描逻辑 | 顶部操作栏 | `tests/test_ui.py` |
| **RequirementPanel** / `self.update_all_btn` | `clicked` | `self._update_all` | 始终启用 | 无 | 全量同步刷新所有需求记录 | 保持全量刷新逻辑 | 顶部操作栏 | `tests/test_ui.py` |
| **RequirementPanel** / `self.status_filter` | `currentIndexChanged` | `self._on_filter_changed` | 始终启用 | `int` | 过滤需求列表表格视图 | 保持原有状态过滤逻辑 | 过滤工具条 | `tests/test_requirement_flags.py` |
| **RequirementPanel** / `self.search_edit` | `textChanged` | `lambda *_: self._req_search_timer.start()` | 始终启用 | `str` | 启动 150ms 单次定时器，超时后触发 `_refresh` | 保持 150ms 搜索防抖与拼音过滤 | 过滤工具条搜索框 | `tests/test_filelib_pinyin_release.py` |
| **RequirementDialog** / `source_btn` | `clicked` | `self._load_documents` | 始终启用 | 无 | 弹出文件选择器，加载需求附件文档 | 保持文档加载解析逻辑 | 对话框附件栏 | `tests/test_ui.py` |
| **RequirementDialog** / `classify_btn` | `clicked` | `self._classify` | 输入了标题/描述 | 无 | 自动判断系统分类 | 保持自动分类规则 | 标题输入框右侧 | `tests/test_ui.py` |
| **RequirementDialog** / `self.description_edit` | `textChanged` | `self._sync_test_points_description` | 始终启用 | 无 | 自动从描述文本提取并同步测试点 | 保持测试点自动同步逻辑 | 描述输入框下方 | `tests/test_requirement_test_points.py` |
| **TestPointsEditor** / `self.add_btn` | `clicked` | `self._add_point` | 输入框非空 | 无 | 新增一条需求测试点记录 | 保持测试点添加逻辑 | 测试点编辑区操作条 | `tests/test_requirement_test_points.py` |
| **TestPointsEditor** / `self.extract_btn` | `clicked` | `self._extract_from_description` | 描述包含条目 | 无 | 正则解析描述提取测试点并追加 | 保持提取解析逻辑 | 测试点编辑区操作条 | `tests/test_requirement_test_points.py` |
| **TestPointRow** / `self.check` | `toggled` | `self._on_toggled` | 始终启用 | `bool` | 更新测试点完成状态，触发重新计分 | 保持完成状态切换与统计刷新 | 单行测试点左侧 | `tests/test_requirement_test_points.py` |
| **InterfaceDebugPanel** / `self.capture_toggle_btn` | `clicked` | `self._toggle_capture` | 始终启用 | 无 | 启动/停止底层网络抓包服务及系统代理 | 保持抓包开关与系统代理接管 | 顶部主操作栏 | `tests/test_interface_debug.py` |
| **InterfaceDebugPanel** / `self.filter_edit` | `textChanged` | `lambda *_: self._search_timer.start()` | 始终启用 | `str` | 启动 150ms 单次定时器，超时触发 `_rebuild_table` | 保持 150ms 列表检索防抖 | 过滤栏搜索框 | `tests/test_interface_fiddler_workbench.py` |
| **InterfaceDebugPanel** / `self._ingest_flush_timer`| `timeout` | `self._flush_ingest_ui` | 抓包数据入队 | 无 | 220ms 间隔批量刷入抓包表格 | 保持 220ms 批量刷表防抖 | 内部定时器 | `tests/test_capture_restart.py` |
| **OpsLogPanel** / `self.cmd_send_btn` | `clicked` | `self._send_cmd_bar` | 已连接 SSH 会话 | 无 | 发送底部命令栏指令至当前主机终端 | 保持指令下发与终端回显 | 终端输入栏右侧 | `tests/test_ssh_terminal.py` |
| **OpsLogPanel** / `self.server_toggle_btn` | `clicked` | `self._open_server_manage_dialog` | 始终启用 | 无 | 唤起 `ServerManageDialog` 进行主机管理 | 保持弹窗管理服务器 | 顶部服务器选择条 | `tests/test_ops_ssh.py` |
| **AiWorkbenchPanel** / `self.run_btn` | `clicked` | `lambda: self._run_sql(reset=True)` | 处于连接有效状态 | 无 | 有选中执行选中；无选中执行光标所在单句 (`statement_at_cursor`) | **P09B覆盖**: 有选中执行选中；无选中执行编辑器全文；移除右上角多余执行按钮 | SQL 编辑器工具条 | `tests/test_sql_workbench_query.py` |
| **AiWorkbenchPanel** / `Ctrl+Return, F5` | `activated` | `lambda: self._run_sql(reset=True)` | 处于连接有效状态 | 无 | 快捷键触发与执行按钮一致的查询逻辑 | **P09B覆盖**: 快捷键触发与执行按钮一致的选择区/全文逻辑 | 全局快捷键 | `tests/test_sql_guard.py` |
| **AiWorkbenchPanel** / `self.conn_new_btn` | `clicked` | `lambda: self._edit_connection(new=True)` | 始终启用 | 无 | 弹出 `ConnectionDialog` 新建连接配置 | 保持新建连接流程 | 库对象树顶部 | `tests/test_connection_dialog.py` |
| **AiWorkbenchPanel** / `self.conn_edit_btn` | `clicked` | `lambda: self._edit_connection(new=False)` | 选中已有连接 | 无 | 弹出 `ConnectionDialog` 编辑当前连接配置 | 保持编辑连接流程 | 库对象树顶部 | `tests/test_connection_dialog.py` |
| **AiWorkbenchPanel** / `self.scan_cancel_btn` | `clicked` | `self._cancel_scan` | 正在执行结构扫描 | 无 | 取消后台 Schema 扫描线程 | 保持取消扫描中断机制 | 库对象树操作条 | `tests/test_ai_workbench_db_scan.py` |
| **AiWorkbenchPanel** / `self.all_btn` | `clicked` | `self._on_all_or_cancel` | 结果表格有数据 | 无 | 执行全量结果抓取或取消全量抓取 | 保持全量抓取与取消机制 | 结果工具条 | `tests/test_sql_workbench_query.py` |
| **ModelChatPanel** / `self.send_btn` | `clicked` | `self._on_action_clicked` | 文本非空或推理中 | 无 | 空闲触发 `_send()`；推理中触发 `_stop()`；同步 Worker 经 `completed.emit` 回传 | 保持单一动作按钮状态切换；保持 Worker 接口 | 输入框右下角 | `tests/test_model_chat_harness.py` |
| **AgentWorkbenchPanel** / `self.send_btn` | `clicked` | `self._on_action_clicked` | 文本非空或运行中 | 无 | 空闲触发 `_send()`；运行中触发 `_stop()` | 保持单一动作按钮状态切换 | 任务输入区右下角 | `tests/test_agent_runtime.py` |
| **SettingsPanel** / `self.reset_layout_btn` | `clicked` | `self._reset_layout_prefs` | 始终启用 | 无 | 清除 `splitter_prefs` 并恢复默认布局比例 | 保持分栏偏好重置逻辑 | 设置页外观卡片 | `tests/test_splitter_prefs.py` |
| **SettingsPanel** / `self.edit_shortcuts_btn` | `clicked` | `self.edit_floating_shortcuts.emit`| 始终启用 | 无 | 发射信号唤起 `FloatingShortcutsEditor` | 保持悬浮快捷配置弹窗触发 | 设置页悬浮窗卡片 | `tests/test_settings_theme_migration.py` |
| **FloatingShortcutsEditor** / `self.done_btn` | `clicked` | `self._finish` | 勾选 4~6 项 | 无 | 保存快捷配置并触发 `saved` 信号与 `accept()` | 保持 4~6 项保存与即时生效 | 弹窗底部操作条 | `tests/test_quick_panel_lifecycle.py` |
| **FloatingShortcutsEditor** / `self.restore_btn` | `clicked` | `self._restore_defaults` | 始终启用 | 无 | 重置会话选择为默认推荐快捷列表 | 保持恢复默认推荐列表 | 弹窗底部操作条 | `tests/test_quick_panel_lifecycle.py` |
| **MainWindow** / `closeEvent` | `closeEvent` | `self._handle_close / self.closeEvent` | 触发窗口关闭 | `QCloseEvent`| 依配置直接退出或弹出 `CloseActionDialog` 询问 | 保持窗口关闭拦截与记住选择 | 主窗口系统框架 | `tests/test_main_window_self_refs.py` |

---

## 5. 交付与验证基线

1. **零生产侵入保证**: 本文档为纯规格与基线修正交付物，严格遵循“功能逻辑与回调完全不变”的最高红线。
2. **源码符号一致性**: 文档内所有类名、模块路径、属性变量名、信号名称及定时器数值均已通过 Python 解释器与 AST 静态索引的机械校验。
3. **测试引用真实性**: 矩阵内引用的所有测试文件（如 `tests/test_release_ui.py`、`tests/test_sql_workbench_query.py` 等）均已确认真实存在于工程测试套件中。
4. **后基线覆盖留档**: 产品负责人批准的 SQL 执行范围改造 (`DATA_CENTER_SQL_EXECUTION_SCOPE`) 已作为正式需求文档化，锁定制图于 `PRISM-UI-P09B` 阶段落地。
