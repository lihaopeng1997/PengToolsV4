# PenngTools V4 · P0-2 样式核查交接（2026-08-27）

> **范围**：需求管理 → 文件库壳层 → 升级准备 → 接口文档更新 → 日报  
> **边界**：只做样式（间距 / 字号 / Token / 布局骨架 / 响应式）。未加功能、未加入口、未改按钮主次、未加 chip。  
> **流程**：本文件供你开软件核对；**通过后你再开下一批**，本文不预设下一步。

---

## 1. 本轮改动文件（样式相关）

| 文件 | 样式类改动摘要 |
|------|----------------|
| `ui/responsive.py` | `editor_min_height` `180→240`；`apply_splitter_orientation` 默认 `min_editor=240` |
| `panels/requirement_panel.py` | L3 `page-filter-bar`；左右 min=`REQ_LEFT_MIN/REQ_RIGHT_MIN`；页间距 Token；文件库行高 36；action-card 间距；`install_splitter_prefs`；窄屏 min 回退 |
| `panels/sql_panel.py` | 页/Tab 间距 Token；SQL 输入与预览编辑区 min 高 240；`apply_layout_mode` 同步间距与编辑区 |
| `panels/docx_panel.py` | 页间距 Token；主/编辑 splitter 挂名 + `install_splitter_prefs`；浏览器≥240 / 工作区≥520；SQL 编辑区≥240；窄屏堆叠 |
| `panels/personal_panel.py`（日报） | 页间距 Token；历史/编辑 splitter prefs；左≥240 / 右≥520；「今日完成」≥240；补 `apply_layout_mode` |
| `resources/style.qss` | 文件库树 `::item` min-height `22→36`、padding 对齐行高 |
| `tests/test_release_ui.py` | 文件库行高断言 `24→36`（对齐样式规格） |

**未改（刻意）**：九按钮文案/顺序/启用逻辑；需求/升级/文档/日报主次按钮角色；任何 chip 文案；业务保存/SVN/SQL 执行路径。

---

## 2. 按页样式 Diff（只列 QSS / Token / 间距 / 字号 / 布局 / 断点）

### 2.1 需求管理

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | 固定 `12` | `SPACING_PAGE=16`，`apply_layout_mode` → `page_spacing_for_mode`（wide/std 16 · compact 12 · narrow/low 10） |
| 筛选条 objectName | `req-filter-card` | `page-filter-bar`（L3 Token；旧选择器仍留兼容） |
| 左树 minWidth | `200` | `REQ_LEFT_MIN=260`（narrow 时临时 `240`） |
| 右详情 minWidth | `360` | `REQ_RIGHT_MIN=520`（narrow 时临时 `360`） |
| 左树卡片 padding | `10,10,10,10` | `12,10,12,12` |
| 详情 Tab minHeight | `200` | `240` |
| 分隔条 | 仅自存 `requirement_ui` | + `install_splitter_prefs`（page=`requirement`，min `[260,520]`，双击/方向键/夹紧） |

### 2.2 文件库壳层（同页 Tab，九按钮不动）

| 项 | 前 | 后 |
|----|----|----|
| action-card margins | `6,4,6,4` / spacing `4` | `8,6,8,6` / spacing `6` |
| 按钮行 spacing | `4` | `6` |
| 文件树行高 delegate | `24` | `TABLE_ROW_H=36` |
| QSS `requirement-file-tree::item` | `min-height:22px; padding:1px 6px` | `min-height:36px; padding:4px 8px` |
| 九按钮 | 顺序/文案/行为 | **未动** |

### 2.3 升级准备

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | `12` | `SPACING_PAGE` + `page_spacing_for_mode` |
| 升级/发版 Tab spacing | `10` | `SPACING_CARD=12` |
| `input_sql` minHeight | `120` | `editor_min_height()=240` |
| 预览三编辑器 minHeight | 无 | `240` |
| `apply_layout_mode` 编辑器列表 | 旧名未命中输入区 | 含 `input_sql` / `upgrade_preview` / `rollback_preview` / `validation_preview` |

### 2.4 接口文档更新

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | `10` | `SPACING_PAGE` + mode 间距 |
| 主/编辑 splitter | 局部变量，无 prefs | `self.main_splitter` / `self.editor_splitter` + `install_splitter_prefs`；handle `8` |
| 浏览器 / 工作区 min | 无 | `240` / `520`（compact/narrow 堆叠时放宽为高度约束） |
| `sql_editor` minHeight | 默认 | `240` |

### 2.5 日报

| 项 | 前 | 后 |
|----|----|----|
| 页根 spacing | 默认 | `SPACING_PAGE` + mode 间距 |
| 历史栏 padding | 默认 | `12,10,12,12` spacing `8` |
| 表单 spacing / margins | spacing `6` | spacing `8`，margins `12,8,12,12` |
| 左历史 minWidth | 无 | `240` |
| 右编辑区 minWidth | 无 | `520` |
| 「今日完成」minHeight | `200` | `240` |
| splitter | handle `6`，无 prefs | handle `8` + `install_splitter_prefs`；补 `apply_layout_mode`（窄屏上下堆叠） |

### 2.6 共享度量

| 项 | 前 | 后 |
|----|----|----|
| `editor_min_height()` | `180` | `240` |
| `apply_splitter_orientation(..., min_editor=)` 默认 | `180` | `240` |

---

## 3. 请你开软件重点看（P0-2）

1. **需求管理**：筛选条是否像统一 L3；左右拖到极限停在约 260 / 520；窄窗左右 min 略松。  
2. **文件库**：九按钮仍是原九个、原顺序；树行更高更易点；横向滚动仍在。  
3. **升级准备**：SQL 输入区明显更高（≥240）；Tab 内卡片间距更匀。  
4. **接口文档**：左右分隔可持久化/双击复位；SQL 区高度够用；窄屏浏览器与编辑上下堆。  
5. **日报**：历史与编辑分隔同套交互；「今日完成」更高；保存按钮仍在日期行（未搬页头）。

---

## 4. 定向验证（本机已跑）

- `outputs/_p0_2_style_smoke.py` → `P0-2 smoke OK`
- `tests.test_requirement_splitter`（4）
- `tests.test_release_ui` 文件库两项（含行高 36）
- `tests.test_daily_report_upgrade...test_completed_editor_takes_most_vertical_space`
- `tests.test_page_skeleton`（6）

---

## 5. 交付产物

打包与提交在交接后执行；路径以当时 `resources/build_info.json` 与 `PengToolsHub_Offline_Setup.zip` 为准。
