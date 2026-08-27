# PenngTools V4 UI · P0 三项阻断核查交接

**日期**：2026-08-27  
**对应提交**：本交接落盘时以仓库 `main` 最新提交为准（含本轮修正）  
**范围**：仅闭环此前评审点名的 **三项 P0 阻断**；**不包含** P0-2，也不预设下一步。  
**基线**：`PenngTools_V4_Private_全套UI需求文档_C方向_V1.2.md` §8.2–8.4、§9.12；页面设计稿 C 方向 V1.2。

---

## 0. 本轮态度与边界

按你的反馈已纠正方向：

1. **不抢跑 P0-2**；本交接只谈三项阻断是否可核。
2. **撤回功能扩散**：接口排查新增的脱敏 callout、认证状态行、cURL/Postman/生成草稿入口、左栏「本地接口模板/发送记录」入口已删除或重新隐藏冻结。
3. **本轮目标是样式/骨架/响应式闭环**，不是再堆文案芯片或新入口。

---

## 1. 阻断项 A — 请求验证两行紧凑上下文（纯样式）

### 实现状态

| 核查点 | 状态 | 证据位置 |
|---|---|---|
| Tab 名「请求验证」 | 已落地 | `panels/interface_debug_panel.py` `setTabText(3, …)` |
| 两行骨架：环境+Base+方法 / URL+HTTPS+发送 | 已落地 | `#request-verify-context` |
| 发送动态文案 `发送 · 到 {host}` / `选择环境后发送` | 已落地 | `_rt_refresh_send_label` |
| 非凸起大卡；控件最小高 ≥36 | 已落地 | QSS + `setMinimumHeight(36)` |
| 内边距 12/10、行距 8 | 已落地 | `request_verify_context` layout margins/spacing |
| Token：`SURFACE`/`BORDER`/`TEXT_MUTED`/`CONTROL_HEIGHT_COMPACT` | 已接入 QSS | `resources/style.qss` `#request-verify-context` |

### 已撤回的功能扩散

- `overview_redact_callout`：已移除  
- `verify_danger_callout` / `verify_auth_status`：已移除  
- `copy_curl_btn` / `copy_postman_btn` / `gen_draft_btn`：重新 **hide 冻结**  
- 左栏「本地接口模板 / 本地发送记录」：已移除  

### 请你目视核

1. 请求验证区是否像设计稿的**两行紧凑条**，而不是多行表单。  
2. 字号/间距是否与稿面接近（Caption 12、控件 36 高）。  
3. 是否**看不到**草稿/认证/脱敏新增入口。

---

## 2. 阻断项 B — 三技术页真实窄屏回退

依据 §8.2：Narrow（960–1099）不得维持不可读三栏；应堆叠 / Tab / 显式开关。

| 页面 | 窄屏行为 | 代码 |
|---|---|---|
| 接口排查 | Compact/Narrow 将 `mid_splitter` 改为**上下堆叠**；详情编辑区最小高 240；分隔按档位 `install_splitter_prefs` | `apply_layout_mode` |
| 日志/SSH | Narrow：**垂直**主分隔；终端侧最小高 220；Compact 保留左右但抬 min 宽 | `ops_log_panel.apply_layout_mode` |
| SQL 控制台 | Narrow：默认只留编辑器主区；「显示对象目录 / 显示 AI 助手」显式打开；中区 min 宽 360；禁止常驻三栏 | `ai_workbench_panel.apply_layout_mode` + `narrow_chrome` |

### 请你目视核

在逻辑宽约 **960–1099**（或强制布局档 Narrow）下：

1. 接口排查：会话与详情是否上下堆叠，而不是挤扁的左右三缝。  
2. 日志/SSH：是否上下堆叠，终端区是否仍可操作。  
3. SQL：默认是否单主区；目录/助手是否要手动打开。

---

## 3. 阻断项 C — Splitter 全量接入

`ui/splitter_prefs.py` 能力清单：

| 能力 | 状态 |
|---|---|
| handle ≥6px、`setChildrenCollapsible(False)` | 有 |
| 双击复位默认比例 | 有 |
| 250ms 防抖 | 有 |
| `accessibleName` | 有 |
| 方向键微调 + Home 复位 | 有 |
| DPI 缩放后 min 夹紧 | 有 |
| 持久化键 `pageId\|tab\|bucket` → `data/layout_splitters.json` | 有 |

### 本轮补齐

- **接口排查** `mid_splitter` 已改为走 `install_splitter_prefs`（`page_id=interface-debug`），并与原有 `ui_prefs.splitter_sizes[mode]` 写入并存。  
- SQL / 日志主分隔此前已接入；本轮强化了 **min_sizes 与窄屏下限**。

### 请你操作核

1. 拖动分隔条 → 松开 → 切换窗口档（或缩放）→ 再回来，比例是否按档恢复。  
2. 双击 handle 是否回默认。  
3. 焦点在分隔条时左右/上下方向键是否微调。  
4. 拖到极限是否停在 min，而不是拖没。

---

## 4. 定向测试（本机已跑）

- `tests.test_request_verify_layout`（含：两行高度、功能入口冻结断言、窄屏 SQL 开关）  
- `tests.test_splitter_prefs`（双击、夹紧、accessibleName、方向键）  
- `tests.test_page_skeleton.PrimaryActionTests`（每页 primary ≤ 1）  
- `tests.test_capture_restart`（停止后再开始监听的回归，与样式无关但同模块）

敏感扫描与离线包：本交接落盘时按交付节奏执行；产物路径以 `resources/build_info.json` 为准。

---

## 5. 明确未宣称完成的事项

- **整站像素级视觉翻新**未宣称完成。  
- **P0-2 需求交付链路**未开工、不在本交接范围。  
- **DPI 125%/150% 真机走查**仍须你在本机勾选（见既有矩阵文件，仅作工具，不代替你的核查结论）。  
- 页头文案/主次按钮若与稿面仍有差，**可在样式轮继续收**；但本交接不以此代替三项阻断。

---

## 6. 核查结论栏（留给你）

| 项 | 通过 / 不通过 | 备注 |
|---|---|---|
| A 两行请求验证（样式） |  |  |
| B 三页真实窄屏回退 |  |  |
| C Splitter 全量接入 |  |  |
| 功能扩散已撤回 |  |  |

**规则确认**：你勾选通过后，才允许进入后续批次；在此之前我不会自行推进 P0-2 或其它模块。
