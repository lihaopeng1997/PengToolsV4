# PengToolsHub V4 UI 重构 — 接手提示词（直接复制可用）

> 用途：交给接手人（AI 助手或开发）作为开工指令。复制「正文」整段发给接手人即可。
> 配套阅读（按需）：`PenngTools_V4_UI重构_整体进度与后续交接_2026-08-27.md`

---

## 正文（复制这段）

你是 PengToolsHub V4 Private（Windows 离线 PyQt6 桌面应用）UI 重构项目的接手人。上一任已完成 P0 阶段大部分样式壳层，现在由你继续。开工前请先完整阅读以下交接文档，再动手：

**必读文档（按顺序）**
1. `deliverables/product-strategy/PenngTools_V4_Private_全套UI需求文档_C方向_V1.2.md` —— 权威 UI 需求基线（含品牌名、四主题 Token、各页面规范）
2. `deliverables/product-strategy/PenngTools_V4_Private_全套页面设计稿_C方向_V1.2.html` —— 权威视觉基线（冻结设计稿）
3. `deliverables/product-strategy/PenngTools_V4_UI重构_Grok实施交接_V1.0.md` —— 上一任的整体路线图与分批规划
4. `deliverables/product-strategy/PenngTools_V4_UI重构_整体进度与后续交接_2026-08-27.md` —— 当前进度快照、未完成项、收尾清单（以这份为准）

**当前进度（截至 2026-08-27）**
- ✅ P0-0 全局设计系统（四主题 Token、四层骨架、按钮角色、Loading、Splitter、响应式）
- ✅ P0-1 高风险技术页样式壳层（接口排查 / 日志 / SQL 控制台，57 测试全过）
- ✅ P0 三项阻断（两行请求验证 / 窄屏回退 / Splitter 全量接入与持久化，**待用户勾选通过**）
- ✅ P0-2 需求交付链路样式（需求 / 文件库 / 升级 / 接口文档 / 日报，50 测试全过）
- ⬜ P0-3 TamengAgent 后台（快照 V2、证据链、AI 助手集成）—— 规格见 `TamengAgent_SQL_Schema_开发规格_V1.0.md`
- ⬜ P1（格式工具 / 加解密 / 证件 / 命令库 / 模型对话 / 设置 + 四主题视觉收口）
- ⬜ P2（非阻断增强）

**你接手后必须按顺序做的 4 件收尾（未完成，最高优先级）**
1. **品牌名切换**：`config.py` 的 `APP_NAME` 仍是旧值 `'PengToolsHub'`，V1.2 基线已定为 `PenngTools`。同步检查 spec 文件、打包脚本、EXE 产物命名。
2. **文件库按钮回归确认**：需求管理 → 文件库，九按钮（打开目录/刷新/更新/添加文件/新建文本/锁定/解锁/回滚/提交）必须完整可见、可点、顺序正确。
3. **P0 三项阻断勾选**：`PenngTools_V4_UI_P0阻断三项_核查交接_2026-08-27.md` 结论栏待用户勾选通过，通过后才放行 P0-3。
4. **离线打包**：`scripts/build_release.ps1`，上次因软件运行中被安全机制拦下，需先关闭软件再重跑。

**下一步主任务：P0-3 TamengAgent**（如用户确认放行）

---

## 红线（不可逾越）

1. **只改样式，不改业务逻辑**：本轮是 UI 重构，禁新增功能入口、禁改按钮主次导致的行为变化、禁加 chip/提示语、禁动后端与数据流。
2. **文件库九按钮布局不动**：需求管理文件库那排按钮（横向滚动 + 28px 高度）已修复定型，禁止再改高度/边距/滚动逻辑。
3. **TamengAgent 不露名、不自动执行**：后台身份与动作必须隐去真实名称，任何 AI 动作不得在无用户确认下自动执行。

## 每批完成后的闭环（缺一不可）

改代码 → 定向测试（只跑本次改动覆盖的）→ 敏感扫描（`scripts/scan_release_secrets.py`）→ 离线构建（`build_release.ps1`）→ 构建信息/产物核对 → 产出交接文档 → 精确提交与推送。

## 环境

- 测试运行：`C:/Users/Lenovo/.workbuddy/binaries/python/envs/pengtools/Scripts/python.exe -m unittest tests.test_xxx -v`
- 打包：系统 Python 3.12（`D:/development/tools/Python312`，含 PyInstaller 6.11），`build_release.ps1` 调用裸 `python`，需把 `D:\development\tools\Python312\Scripts` 加入 PATH。
- 关键文件：`panels/requirement_panel.py`（文件库）、`panels/interface_debug_panel.py`（接口排查）、`tools/http_capture.py`（抓包引擎）、`ui/`（主题/组件）。

## ⚠️ 重要告警：git 基线缺失

本地 git 仓库 `main` 分支**目前没有任何 commit**，所有文件都处于 `git add` 暂存状态。交接文档里提到的 commit hash（`d69240c`、`44353e4` 等）是上一任在别处环境提交的，**未同步到本地**。代码文件本身是最新的，但请你**第一步先建提交基线**（`git commit`），否则后续没有可追溯历史。
