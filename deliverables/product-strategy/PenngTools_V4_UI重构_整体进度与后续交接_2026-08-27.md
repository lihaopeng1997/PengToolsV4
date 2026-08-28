# PenngTools V4 全量 UI 重构 · 整体进度与后续开发/检查交接

**文档版本**：V1.0
**落盘日期**：2026-08-27
**适用对象**：接手本项目的下一位开发者 / 核查人
**一句话结论**：P0-0 / P0-1 / P0-2 的样式壳层与 P0 三项阻断均已实施并通过定向测试；你接手后需先补完三件收尾（见 §6），再按 P0-3 → P1 → P2 顺序推进，每批走「改 → 定向测试 → 敏感扫描 → 离线构建 → 产物核验 → 交接文档」闭环。

---

## 1. 项目定位与硬边界（先读，别踩红线）

本项目是 **Windows 离线 PyQt6 桌面应用**（Python 3.12 系统解释器 + 项目内 venv），产品品牌为 **PenngTools**，作者署名 `Lihp` 仅出现在关于/版本区。

**三条不可逾越的硬约束**（违反即打回）：

1. **只改样式，不改业务**。UI 重构仅允许动：布局、间距、QSS Token、容器层级、控件尺寸、显示密度、状态展示、Tooltip、无障碍、响应式收纳、Splitter 比例、表格列宽、Loading 表现。**严禁**借重构改变网络发送、SSH 执行、SQL 执行、SVN 操作、删除语义、密码/密钥/Token 存储、会话生命周期、数据路径、导出/权限规则。
2. **文件库九按钮原样保留**：`打开目录 → 刷新 → 更新 → 添加文件 → 新建文本 → 锁定 → 解锁 → 回滚 → 提交`。文字、顺序、功能、图标、启用条件、快捷操作、确认流程**一律不得调整**，只允许统一外观/间距 + 窄屏横向滚动。
3. **TamengAgent 只做后台**。SQL 控制台用户可见名称永远是「AI 助手」，草案页签永远是「SQL 草案 · 未执行」；TamengAgent 是后台 Schema 证据编排器，**禁止**显示为用户可见名称，**禁止**自动执行 SQL，**禁止**猜测字段名（必须由快照证据链支撑）。

完整硬约束清单见 `deliverables/product-strategy/PenngTools_V4_UI重构_Grok实施交接_V1.0.md` §3、§8。

---

## 2. 必读基线文档（接手后第一件事）

| 文档 | 作用 |
|------|------|
| `PenngTools_V4_Private_全套UI需求文档_C方向_V1.2.md` | **视觉与交互的权威基线**（注意：是 V1.2，不是 V1.1） |
| `PenngTools_V4_Private_全套页面设计稿_C方向_V1.2.html` | 页面设计稿（浏览器打开即可预览） |
| `PenngTools_V4_UI重构_Grok实施交接_V1.0.md` | 总实施交接（P0→P2 路线图 + 每批要求） |
| `TamengAgent_SQL_Schema_开发规格_V1.0.md` | P0-3 后台能力的开发规格 |
| `ui-control-copy-audit-2026-08-26.md` | 控件文案审计（按钮/Tab/空态统一文案来源） |
| `AGENTS.md` | 项目级协作约定 |

> ⚠️ **版本坑**：历史上存在 V1.0 / V1.1 / V1.2 三版设计稿。**当前权威基线是 V1.2**。V1.2 文件名是 `PenngTools`（多了个 n），V1.1 是 `PengToolsHub`（品牌名旧值）。任何比对一律以 V1.2 为准。软件 `config.py` 里 `APP_NAME` 当前仍是 `'PengToolsHub'`（旧值），**品牌名切换未做**——这是收尾待办之一（见 §6.1）。

---

## 3. 已完成的进度（截至 2026-08-27）

### 3.1 批次完成状态总览

| 批次 | 内容 | 状态 | 证据 |
|------|------|------|------|
| **P0-0** 全局设计系统 | 四主题 Token、四层页面骨架、按钮角色、空态、风险确认、Loading、Splitter、响应式基础组件 | ✅ 已完成 | `ui/theme_manager.py` `ui/splitter_prefs.py` `ui/responsive.py` `ui/field_metrics.py` + `tests/test_splitter_prefs.py` `test_page_skeleton.py` 等 |
| **P0-1** 高风险技术页 | 接口排查 → 日志/SSH → SQL 控制台（AI 助手 UI）样式壳层 | ✅ 样式壳层完成 | `PenngTools_V4_UI_P0-技术三页壳层_样式核查交接_2026-08-27.md`（57 测试全过） |
| **P0 三项阻断** | 两行请求验证 / 三技术页真实窄屏回退 / Splitter 全量接入 | ✅ 已实施，**待用户勾选通过** | `PenngTools_V4_UI_P0阻断三项_核查交接_2026-08-27.md` |
| **P0-2** 需求交付链路 | 需求管理 → 文件库 → 升级准备 → 接口文档更新 → 日报（样式） | ✅ 样式完成（50 测试全过），**但发现文件库按钮裁切 BUG 已修** | `PenngTools_V4_UI_P0-2_样式核查交接_2026-08-27.md` |
| **P0-3** TamengAgent 后台 | 快照 V2、证据编排、AI 助手集成、安全回归 | ⬜ **未开工** | `TamengAgent_SQL_Schema_开发规格_V1.0.md` |
| **P1** 其余页面 | 格式工具、加解密、证件/VIN、命令库、模型对话、设置 + 四主题/轻玻璃/悬浮Tab收口 | ⬜ 未开工 | — |
| **P2** 非阻断增强 | 低频页细节、局部动效、辅助信息 | ⬜ 未开工 | — |

### 3.2 已落地的关键样式能力

- **四主题系统**：Calm / Clear / Warm / Black，`ThemeManager` 单例，`apply()` 统一 QPalette + QSS + Fusion。
- **四层页面骨架**：L1 页头（图标+标题+副标题+状态+至多 1 主操作）/ L2 工具栏 / L3 筛选条（`page-filter-bar`）/ L4 内容区。
- **Splitter 全量接入**：`ui/splitter_prefs.py`，支持 handle≥6px、`setChildrenCollapsible(False)`、双击复位、250ms 防抖、accessibleName、方向键微调、DPI 缩放夹紧、按 `pageId|tab|bucket` 持久化到 `data/layout_splitters.json`。
- **响应式四档**：Wide≥1440 / Standard 1280–1439 / Compact 1100–1279 / Narrow 960–1099；窄窗切上下堆叠/Tab/显式开关，不硬压三栏。
- **统一 Token 与字号**：`SPACING_PAGE=16` / `SPACING_CARD=12`、`editor_min_height()=240`、`TABLE_ROW_H=36`、`BTN_COMPACT_H=28`、`BTN_COMPACT_MIN_W`。

### 3.3 本轮（上一轮）额外修复的两个 BUG

这两个 BUG 是验收时新发现的，已修复并通过测试，**接手人需知道它们的存在与根因**，避免重复踩坑：

1. **文件库九按钮「展示一半」**（`panels/requirement_panel.py`）
   - 根因：`action_row` 用了 QHBoxLayout **默认 9px 上下边距**把按钮下推 + 横向滚动条实际高 14px（非假设的 10px）压扁 viewport。
   - 修复：`action_row` 上下 margin 归零；滚动条高度按 `sizeHint` 动态计算（不再写死 `+12`）；`_clamp_file_library_action_heights()` 固定按钮 28px。
   - 验证：离屏渲染 9 按钮全部 `fullyVisible=True`。

2. **接口排查「停止后再点监听不好使」**（`panels/interface_debug_panel.py` + `tools/http_capture.py`）
   - 根因：`worker.stop()` 丢后台线程，`master.shutdown()` 异步调度只 join 2 秒，mitmproxy 端口 8899 释放慢；再启动时新引擎抢不到端口，`wait_ready(12s)` 超时 + 重试，按钮卡死约 25 秒。
   - 修复：`HttpCaptureWorker.stop()` 新增 `_wait_port_released(3s)` 主动等端口释放；`_start_local_proxy` 启动前做端口占用预检（最多等 4 秒）。
   - 验证：`tests/test_capture_restart.py` 新增 2 项端口释放用例，共 5 项全过。

---

## 4. 关键代码地图（接手人必读）

| 文件 | 职责 | 备注 |
|------|------|------|
| `ui/theme_manager.py` | 四主题单例 | 所有颜色走 Token |
| `ui/splitter_prefs.py` | Splitter 持久化/无障碍/夹紧 | P0 阻断 C 核心 |
| `ui/responsive.py` | 响应式档位 + `apply_layout_mode` | `editor_min_height=240` |
| `ui/field_metrics.py` | 控件尺寸常量（`BTN_COMPACT_H=28` 等） | `size_compact_button` |
| `resources/style.qss` | 全局 QSS | 大量 `#objectName` 选择器 + Token 变量 |
| `panels/interface_debug_panel.py` | 接口排查（~4500 行，最大文件） | 监听链路 `_toggle_capture`/`_stop_listen`/`_start_local_proxy` |
| `panels/requirement_panel.py` | 需求管理 + 文件库 | `_clamp_file_library_action_heights` |
| `panels/ops_log_panel.py` | 日志排查（~3600 行） | 终端 + SSH + 导出 |
| `panels/ai_workbench_panel.py` | SQL 控制台 + AI 助手 | P0-3 TamengAgent 集成点 |
| `panels/sql_panel.py` / `docx_panel.py` / `personal_panel.py` | 升级准备 / 接口文档 / 日报 | P0-2 已改 |
| `tools/http_capture.py` | mitmproxy 抓包引擎 | `stop()` 端口释放逻辑 |
| `tools/schema_snapshot.py` | Schema 快照（P0-3 依赖） | 快照 V2 待做 |
| `tools/ai_sql_draft.py` / `ai_object_context.py` | SQL 草案 / 对象上下文 | TamengAgent 相关 |

---

## 5. 环境与运行方式（接手人必读）

- **Python**：系统 `D:\development\tools\Python312\python.exe`（3.12，**打包必须用它**，含 PyInstaller 6.11.1）。
- **venv**：`C:\Users\Lenovo\.workbuddy\binaries\python\envs\pengtools`（PyQt6 可用，跑测试/诊断用这个）。
- **运行软件**：项目根 `python main.py`（或已打包的 `dist\PengToolsHub.exe`）。
- **打包**：`scripts/build_release.ps1`，调用裸 `python`，需把 `D:\development\tools\Python312` 和 `...\Scripts` 加入 PATH。
- **敏感扫描**：`scripts/scan_release_secrets.py`（每批必跑，要求 0 高危 0 警告）。
- **诊断技巧**：Windows 下 `/tmp` 会映射到 `D:\tmp`，临时脚本请写在项目根目录，跑完删除。

---

## 6. 接手后必须完成的收尾（按优先级）

### 6.1 品牌名切换（未做，待办）

`config.py` 第 15 行 `APP_NAME = 'PengToolsHub'` 仍是旧品牌名。V1.2 基线已改为 **PenngTools**（需求文档 §934「品牌统一为 PenngTools」）。切换时注意：
- 改 `config.py` 的 `APP_NAME` 后，检查所有引用 `APP_NAME` 的地方（窗口标题、关于页、版本信息、错误提示、安装包命名）。
- 确认 `.spec` 打包配置、`build_release.ps1` 里的 EXE 名是否需同步（当前 EXE 是 `PengToolsHub.exe`）。
- 这是「品牌名」与「产品名」的一致性收尾，属于样式/文案范畴，**不涉及业务逻辑**。

### 6.2 文件库按钮回归确认（用户验收未闭环）

上一轮修复了「展示一半」，但**用户尚未开软件最终确认**。接手后请用户开软件看「需求管理 → 文件库」九按钮是否完整可见、可点击、顺序正确。

### 6.3 P0 三项阻断的「通过/不通过」勾选（用户验收未闭环）

`PenngTools_V4_UI_P0阻断三项_核查交接_2026-08-27.md` §6 的核查结论栏还是空的。**规则**：必须等用户勾选通过 A/B/C 三项 + 确认「功能扩散已撤回」，才允许进入后续批次。在此之前不得自行推进 P0-3 或其它模块。

### 6.4 离线打包与产物核验（上轮未完成）

上一轮 `build_release.ps1` 因「PengToolsHub.exe 正在运行」被安全机制拒绝（脚本不会强制结束进程）。接手后需：
1. 让用户**先关闭正在运行的软件**。
2. 重跑 `build_release.ps1`，产出 `dist\PengToolsHub.exe` 和 `PengToolsHub_Offline_Setup.zip`。
3. 核对 `resources/build_info.json` 的版本/构建时间/提交 hash（当前显示 4.27 / 2026-08-27 13:52）。

---

## 7. 后续开发路线图（P0-3 → P1 → P2）

### 7.1 P0-3：TamengAgent 后台能力（下一步主任务）

**依据**：`TamengAgent_SQL_Schema_开发规格_V1.0.md`，按 P0-4A → P0-4B → P0-4C → P0-4D 实施。

核心目标：把「自然语言字段含义 → 真实字段」的证据链做硬。典型问题——用户输入「查询 prpcmain 中创建日期倒序」，模型可能输出不存在的 `createddate`，而真实快照字段是 `CREATED_DATE`。TamengAgent 要让 SQL 用的表/字段/索引/方言都有**可回查的真实快照证据**。

关键规则：
- 仅基于**当前连接的有效 Schema 快照**检索表/字段/注释/类型/主键/索引。
- 快照是唯一事实源；`SnapshotGate` 校验连接/指纹/状态/版本/截断。
- 状态机：`SNAPSHOT_MISSING` / `SNAPSHOT_STALE` / `SNAPSHOT_V1` / `READY` / `DRAFT_READY`。
- 快照 V2：索引元数据 `index_metadata_status`、`truncated` 截断标记；V1 快照可浏览但不能做索引建议。
- **禁止**猜字段、联网取 Schema、自动执行 SQL；生成与执行是独立按钮、独立路径。
- 用户显式选中的对象/字段是最高优先级证据。

### 7.2 P1：其余页面 + 体验收口

范围：格式工具、加解密、证件/VIN、命令库、模型对话、设置。同时补四主题、轻玻璃、悬浮 Tab 的全局统一，以及所有 Splitter/列宽持久化和高 DPI 回归。

### 7.3 P2：非阻断增强

低频页细节、局部动效、辅助信息。**不得**进入新业务能力、云同步、遥测、浏览器内核或自动化执行。

---

## 8. 每批开发的标准化流程（必须照做）

接手后每一批都走这套闭环，缺一步不算完成：

1. **读**：先读本批页面源码、对应定向测试、V1.2 设计稿对应章节。
2. **判**：只改本批覆盖的公共组件/页面；发现要改业务语义就停下单独说明。
3. **测先行**：先补/调整定向测试，再改页面。
4. **实现**：只改样式（布局/间距/Token/字号/骨架/响应式）。
5. **验尺寸**：`1440×900`、`1280×800`、`1100×720`、`960×640` + `100%/125%/150%` DPI 下的主要状态。
6. **必测态**：默认态、空态、Loading、失败/取消、风险确认、拖拽极限、双击重置、Tab/主题切换、窄窗回退。
7. **跑门禁**：定向测试（`python -m unittest tests.<本批相关> -v`，用 venv 的 python）+ 敏感扫描 `scan_release_secrets.py`（0 高危 0 警告）+ `build_release.ps1` + 构建信息/产物核验。
8. **交接**：写一份样式核查交接文档（放 `deliverables/product-strategy/`），列出改动文件、按页样式 Diff、请用户开软件核对点、完整测试清单、交付产物。
9. **边界汇报**：修改文件、保留功能核对、测试/扫描/构建结果、未完成项、待内网验证项。

**关键约束**：`data/`、Token、密钥、日志、报文、临时截图、构建临时文件、用户本地配置**一律不提交**。

---

## 9. 当前仓库状态告警（重要，务必先读）

接手人打开仓库会看到：**`git log` 报「main 分支还没有任何 commit」**，所有文件处于 `git add` 暂存（staged）状态，但从未 commit。

这是当前工作目录的真实状态：
- **代码是最新的**（含上一轮三处修复：文件库按钮 + 监听端口释放 + 对应测试）。
- **但 git 历史是空的**。交接文档里提到的历史 commit hash（如 `d69240c`、`eec19f8` 等）是 Grok 在其自身环境提交的，**没有同步到这个本地工作目录**。

**建议接手人第一步**：确认代码内容无误后，建立自己的提交基线（`git commit` 一次快照作为起点），后续每个批次一个 commit。这样才有可追溯的历史。是否补建历史、或从远程 `github.com/lihaopeng1997/PengToolsV4.git` 拉取真实历史，由接手人与项目负责人确认。

---

## 10. 一句话交接清单

- ✅ P0-0 设计系统、P0-1 技术三页、P0-2 需求链路、P0 三项阻断：**已实施 + 定向测试全过**。
- 🔧 上一轮已修：文件库按钮裁切、监听端口释放竞态（2 个 BUG）。
- ⏳ 待用户验收闭环：文件库按钮、P0 三项阻断勾选、品牌名切换、离线打包。
- ⬜ 下一步开发：**P0-3 TamengAgent**（先读开发规格）→ P1 → P2。
- ⚠️ 仓库无 git 历史，接手人需先建提交基线。
- 📌 硬红线：只改样式不改业务、文件库九按钮不动、TamengAgent 不露名/不自动执行。
