# PengToolsHub V4.27 Private — Grok 接手完整交接文档

> **用途**：下一位 Grok / 开发者读完本文 + 根目录 `AGENTS.md` 即可直接改代码、跑测试、打私人包、推送 GitHub。  
> **更新**：2026-07-21  
> **界面版本文案**：`V4 Private`（`config.app_version_text()`）  
> **内部版本号**：`4.27`（`resources/build_info.json`）  
> **仓库**：`https://github.com/lihaopeng1997/PengToolsV4.git`  
> **默认分支**：`main`  
> **工作目录**：`D:\development\workspace\Codex\自研软件\PengTools\PengToolsV4`  
> **作者署名**：Lihp  

---

## 0. 接手 10 分钟清单

1. 读 `AGENTS.md`（硬规则）+ 本文（架构与流程）。
2. `git pull origin main`；确认 `resources/build_info.json`。
3. `python -m pip install -r requirements.txt` → `python run.py` 能启动。
4. 改代码前优先 CodeGraph，不要盲扫全库：

```powershell
codegraph status .
codegraph query -p . -l 20 <symbol>
codegraph callers -p . <symbol>
codegraph impact -p . <symbol>
codegraph sync .   # 改完后同步索引
```

5. 只改本轮范围；定向测试；`.\scripts\build_private_release.ps1`；`git commit` + `git push origin main`。
6. **禁止**动标准包 `PengToolsHub_Offline_Setup.zip`；**禁止**把 `data/` 打进安装包或提交 Git。

---

## 1. 产品定位与边界

### 1.1 是什么

Windows **离线**桌面效率工具台（Python 3.12 + PyQt6），服务内网开发/运维日常：

| 能力 | 说明 |
|---|---|
| 工作台 | 最近需求、待升级事项、快捷入口 |
| 需求管理 | 需求/BUG 树、文件库、SVN、SQL 联动 |
| 升级准备 | 多系统 SQL、发版 Excel |
| 日报 / 自我学习 | 日报草稿保留；学习库彩蛋解锁 |
| 网关加解密 | SM2+SM4，敏感内容不落盘 |
| 格式工具 | JSON/XML/SQL/Base64 等 |
| 接口排查（Private） | 本机 MITM 抓包、请求测试、明细导出导入 |

**产品名显示**：`PengToolsHub`（`config.APP_NAME`）  
**口号向 UI 版本**：`V4 Private`（不要再写 `V4.27 Private · 日期 · 日期`）

### 1.2 硬边界（违反即事故）

| 规则 | 说明 |
|---|---|
| 离线 | 无账号、云同步、插件市场、在线更新、遥测、在线 CDN |
| 网络 | 运行时业务代码不引入远程 HTTP/WebSocket/浏览器内核 |
| Private 抓包例外 | 仅 loopback：`127.0.0.1` MITM（mitmproxy）+ 可选 Chromium CDP；**禁止**把代理暴露到局域网 |
| 敏感报文 | 请求/响应/Cookie/Token/Key/**只存内存**；禁止写日志与 JSON |
| 停止 vs 清空 | **停止抓包保留会话**；仅「清空」与退出应用 `clear_session()` |
| 请求测试 | 按用户保存的**环境 Base** 替换 host 后发送（`interface_debug.json` 的 `local_targets`） |
| 数据根 | 唯一 `config.local_data_dir()`：开发=`./data/`，打包=`<exe旁>/data/`；禁止 `_MEIPASS` / 用户主目录 |
| 升级 | 只替换 EXE/程序文件；**不得删除/覆盖**安装目录 `data` |
| 标准包 | `PengToolsHub_Offline_Setup.zip` **禁止**被私人功能改动；日常用 `build_private_release.ps1` |

---

## 2. 仓库与目录架构

```
PengToolsV4/
├── run.py / main_window.py / config.py   # 入口 · 装配 · 配置
├── panels/ · tools/ · ui/                # 界面层 · 逻辑层 · 公共 UI
├── resources/ · data/ · tests/
├── scripts/               # ★ 构建与开发工具（权威位置）
│   ├── build_private_release.ps1
│   ├── build_release.ps1
│   └── build_workbook_seed.py
├── packaging/             # 安装布局说明
├── docs/                  # 架构 / 交接 / UI 需求
├── AGENTS.md · README.md · requirements.txt
├── build_private_release.ps1   # 根目录转发 → scripts/
└── .codegraph/
```

### 2.1 分层约定

```
run.py
  └─ MainWindow（导航 + Stack + 信号总线）
        ├─ panels/*（UI、交互、Loading）
        │     └─ tools/*（纯逻辑、IO、算法）
        └─ ui/*（主题 token、按钮、图标、响应式）
```

- **panels 不直接写复杂算法**；算法/文件/SVN/加解密放 `tools/`。
- **跨模块跳转**经 `main_window` 信号，不互相深层引用对方内部控件。
- **样式**：`resources/style.qss` + `ui/theme_manager.py` token 注入；禁止硬编码大面积颜色（主题会失效）。

---

## 3. 导航 index ↔ Stack（改导航必同步）

| 导航 index | 显示名 | Stack 控件 | 常显？ |
|---:|---|---|---|
| 0 | 工作台 | DashboardPanel | 是 |
| 1 | 证件类型 | CreditCodePanel | 是 |
| 2 | 升级准备 | SqlToolPanel | 是 |
| 3 | 接口文档更新 | DocxUpdatePanel | 是 |
| 4 | VIN | VinPanel | 是 |
| 5 | 网关解密 | GatewayDecodePanel | 是 |
| 6 | 运维助手 | OpsPanel | 是 |
| 7 | 设置 | SettingsPanel | 是（左下角） |
| 8 | 自我学习 | PersonalPanel（学习页） | **彩蛋解锁后** |
| 9 | 日报 | PersonalPanel（日报页） | **常显，禁止再隐藏** |
| 10 | 需求管理 | RequirementPanel | **常显，禁止再隐藏** |
| 11 | 格式工具 | FormatToolsPanel | 是 |
| 12 | 接口排查 | InterfaceDebugPanel | Private 常显 |

**映射注意（容易踩坑）**：

- 导航 8/9 → **同一个** Stack 页 `PersonalPanel`（Stack index 8）
- 导航 10 → Stack 9（RequirementPanel）
- 导航 11 → Stack 10（FormatToolsPanel）
- 导航 12 → Stack 11（InterfaceDebugPanel）

改导航时必须同步：`ui/navigation_model.py`、`main_window` 按钮/Stack、`set_language` 文案、状态栏提示。

彩蛋：仅「自我学习」可隐藏；解锁持久化在 `data/settings.json` 的 `private_unlocked`；密钥与解锁入口勿写进普通 UI 文案。

---

## 4. 核心模块地图（改哪里）

### 4.1 工作台

| 文件 | 职责 |
|---|---|
| `panels/dashboard_panel.py` | 最近需求、**待升级事项**、常用工具 |

**待升级规则（2026-07-21）**：

- 仅：**已填 `planned_online_date`** 且 **日期 ≥ 今天**；
- 排除：已上线/已关闭/已取消/暂停、已有 `actual_online_date`、无上线日、**已过期**；
- **今天上线**：置顶 + 高亮（`dashboard-task-row-today`）。

### 4.2 需求管理

| 文件 | 职责 |
|---|---|
| `panels/requirement_panel.py` | 树、摘要、文件库、SVN、联动按钮 |
| `tools/requirements.py` | 模型、搜索、日报模板、标志位 |
| `tools/svn_workspace.py` | checkout/update/commit/lock、工作区文件枚举 |
| `tools/pinyin_search.py` | 拼音/首字母搜索 |

**文件库**：

- 列：名称 / 类型 / 修改时间 / 大小 / 路径；
- **仅名称列**系统文件图标；
- 列宽 Interactive + 可横向滚动 + 表头可调序；
- 支持外部文件拖入 → 复制到工作副本并尝试 `svn add`。

**右侧布局**：上摘要内容定高，下文件库占满；左右为 `requirement-splitter`。

### 4.3 升级准备 / 发版

| 文件 | 职责 |
|---|---|
| `panels/sql_panel.py` | 升级准备 UI、系统配置 |
| `tools/release_prep.py` | 发版 Excel、SQL 整理 |
| `resources/release_workbook_template.xlsx` | 模板 |

- 发版 Excel **23 列** `RELEASE_HEADERS` 必须一致；
- 开发分支 SVN 可为空，不可阻塞生成；
- 验证 SQL 不进 SVN 提交目录。

### 4.4 日报 / 学习

| 文件 | 职责 |
|---|---|
| `panels/personal_panel.py` | KnowledgeTab + DailyReportTab |
| `tools/daily_reports.py` | 读写日报 JSON、提醒 |
| `tools/personal_knowledge.py` | 学习库 |

**日报（已实现）**：

- 切换日期**保留未保存草稿**（内存 `_drafts`）；
- 「复制为今日」：把当前编辑内容写成今天草稿；
- 需求「写入日报」走 `daily_template()`，不覆盖已有段落。

### 4.5 网关解密

| 文件 | 职责 |
|---|---|
| `panels/gateway_panel.py` | UI；`set_cipher_and_key(cipher, key)` |
| `tools/gateway_crypto.py` | SM2+SM4 加解密 |

密钥/明文/报文默认不落盘、不写日志。

### 4.6 接口排查（Private 重点）

| 文件 | 职责 |
|---|---|
| `panels/interface_debug_panel.py` | 抓包 UI、列表、详情 Tab、请求测试、导出导入 |
| `tools/http_capture.py` | MITM 引擎、会话记录 |
| `tools/ie_proxy.py` | WinINet 代理备份/恢复、证书 |
| `tools/iface_request_test.py` | 环境 Base、URL 重写、解密优先正文、导出/导入、发送 |
| `tools/interface_session_view.py` | 列表列定义、筛选排序 |
| `tools/interface_debug_store.py` | `interface_debug.json` 配置（无报文） |
| `tools/interface_drafts.py` | 旧草稿/cURL/Postman 工具函数（兼容） |
| `tools/browser_debug.py` | CDP 相关（高级，UI 默认隐藏模式） |

**当前交互主路径**：

1. 开始抓包 → 系统代理 loopback → 浏览器重启后访问业务页；
2. 列表 Fiddler 式列；URL 列 Stretch 填空白；
3. 详情：概览 / 请求 / 响应 / **请求测试**；
4. 送入加解密：报文 + 自动提取的 SM4 Key；
5. 导出明细 `pengtools_iface_session_v1`（优先解密明文）；
6. 请求测试：选环境 Base → 替换 host 保留 path → 可发送；
7. 停止抓包**不清列表**；清空按钮才清。

**配置** `data/interface_debug.json` 允许字段：路径、端口、`local_targets`（环境）、证书指纹、代理快照、UI 偏好（列宽/splitter）。**禁止**写报文。

### 4.7 UI 基础设施

| 文件 | 职责 |
|---|---|
| `ui/theme_manager.py` | calm/clear/warm/night token → 注入 QSS |
| `ui/design_system.py` | `apply_button`（primary 图标用 ON_PRIMARY） |
| `ui/icons.py` | 本地 SVG + tint |
| `ui/responsive.py` | 断点、分栏方向、ResponsiveActionBar |
| `ui/confirm_dialog.py` | 统一确认/成功/警告；关闭主窗口决策 |
| `ui/aurora_progress.py` | Loading 浮层 |
| `ui/quick_panel.py` | 悬浮栏 |
| `ui/single_instance.py` | 单实例 |
| `ui/navigation_model.py` | 导航文案/分组 |

**响应式断点（参考）**：Wide≥1440 / Standard 1280–1439 / Compact 1080–1279 / Narrow 960–1079。  
Compact/Narrow 双栏可改垂直；**不重排** splitter 子控件顺序，避免拖拽「反方向」。

**按钮图标**：`apply_button(..., role='primary', icon=...)` 时图标必须浅色（ON_PRIMARY），避免绿底深色看不清。

---

## 5. 数据与配置文件

根目录：`config.local_data_dir()` → 开发 `PengToolsV4/data/`。

| 文件 | 内容 | 注意 |
|---|---|---|
| `settings.json` | 主题、字体、语言、悬浮栏、关闭行为、`private_unlocked` | 升级保留 |
| `requirements.json` | 需求/BUG 全量 | 字段兼容 setdefault |
| `requirement_ui.json` | 分栏尺寸等 UI | |
| `daily_reports.json` | 已保存日报 | 未保存草稿仅内存 |
| `daily_report_settings.json` | 提醒 | |
| `interface_debug.json` | 抓包/环境/UI 偏好 | **无报文** |
| `systems.json` 等 | 系统配置 | 以 config 路径为准 |
| `svn_workspaces/` | 工作副本目录 | 用户数据 |

**JSON 规范**：

- 读：缺字段用默认 / `setdefault`；
- 写：保留未知旧字段；
- 删除类 UI：取消在左、确认在右、**默认焦点取消**。

---

## 6. 关键业务联动

```
需求管理 ──写入日报──► 日报（不覆盖已写）
    │
    ├──准备本次升级──► 升级准备 ──► 发版 Excel（23 列）
    │
    ├──SQL 整理──► main_window 信号
    └──接口文档──► Docx 面板
```

接口排查：

```
抓包会话 ──送入加解密──► 网关面板（cipher + key）
        ──送格式工具──► 格式工具（优先解密明文）
        ──请求测试──► 环境 Base 重写 URL ──发送
        ──导出明细──► JSON ──导入/拖入──► 请求测试回填
```

---

## 7. 开发流程（标准回合）

### 7.1 接到需求后

1. 对照 `AGENTS.md` 判断是否触碰边界（网络/data/标准包/敏感报文）。
2. CodeGraph 定位符号与调用链 / 影响面。
3. 小步改：`tools` 逻辑优先可单测 → `panels` UI → 文案/QSS。
4. 定向测试（见第 8 节）。
5. 若可交付：提交并推送（见 7.3）。
6. 私人包：`.\scripts\build_private_release.ps1`（或根目录转发脚本；若 EXE 被占用先结束 `PengToolsHub` 进程）。
7. 改完代码：`codegraph sync .`。

### 7.2 本地运行

```powershell
cd D:\development\workspace\Codex\自研软件\PengTools\PengToolsV4
python -m pip install -r requirements.txt
python run.py
```

Headless 测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_xxx -v
```

### 7.3 Git 约定

```powershell
git status
# 定向验证通过后
git add <相关文件>   # 不要 add data/、zip 安装包、截图、日志
git commit -m "type(scope): 中文说明"
git push origin main
```

- 远端固定 `origin` → 上述 GitHub 仓库；
- 默认交付后 **push main** 保证可回滚；
- 探索/半成品/用户说先别提交 → 不推；
- 提交信息：完整句子，说明「改了什么、为什么」。

### 7.4 私人包构建

```powershell
# 若 PermissionError 锁文件：
Get-Process PengToolsHub -ErrorAction SilentlyContinue | Stop-Process -Force
.\scripts\build_private_release.ps1
```

产物：

- `dist/PengToolsHub.exe`
- `PengToolsHub_Private_Offline_Setup.zip`（包内 EXE 名为 `PengToolsHub.exe`）
- 构建脚本会写 `resources/build_info.json` 日期戳  
- 会校验**标准包** SHA 未被改动

**不要**日常跑 `build_release.ps1`（标准包）。

---

## 8. 测试策略

- **原则**：本轮改动模块定向测试优先，不强制全量。
- 业务规则（待升级筛选、URL 重写、发版列、导出 kind）必须有函数级断言。
- 面板测试用 `QT_QPA_PLATFORM=offscreen`；涉及 `show_*` 弹窗时需 mock，否则可能卡住。
- 内网 SVN / 真实抓包：自动化无法替代，交付写「待用户内网验证」。

| 测试文件 | 覆盖方向 |
|---|---|
| `tests/test_core.py` | 加解密、核心工具 |
| `tests/test_interface_debug.py` | 接口配置/草稿/待升级 |
| `tests/test_iface_request_test.py` | 环境 Base、导出导入、发送 |
| `tests/test_interface_fiddler_workbench.py` | 列表/详情烟雾 |
| `tests/test_release_ui.py` | 需求文件库、分栏 |
| `tests/test_lazy_ui_selfcheck.py` | 日报草稿/复制为今日 |
| `tests/test_release_prep.py` | 发版 Excel |
| `tests/test_requirement_*.py` | 标志位/分栏等 |

---

## 9. UI / 交互规范（摘要）

1. 统一 QSS + 主题 token；新增颜色走 token。
2. 主操作 `primary-btn`；破坏性 `danger`；次要 secondary/ghost。
3. Loading：`AuroraProgress` 浮层；成功/失败/异常都要结束。
4. 静默任务（文件树刷新等）`show_loading=False`。
5. 分栏：`setOpaqueResize(True)`、`setChildrenCollapsible(False)`；统一把手样式。
6. 表头/列表：避免最后一列后大片空白（抓包列表 URL 用 Stretch）。
7. 中英文：`set_language` 覆盖可见文案。
8. 关于弹窗：励志搞笑文案 + **Lihp**（`MainWindow._show_about`）。

---

## 10. 安全与 Private 检查表（提交前自检）

- [ ] 无远程 host 代理监听、无局域网 bind  
- [ ] 报文/Key 未写入 `data/*.json` 或日志  
- [ ] 停止抓包不清会话；清空/退出才 clear  
- [ ] `interface_debug.json` 仅配置  
- [ ] 安装包未包含用户 `data`  
- [ ] 未修改 `PengToolsHub_Offline_Setup.zip`  
- [ ] 导航/Stack/语言文案同步  
- [ ] 定向测试通过  
- [ ] 需要交付时已 push `main`  

---

## 11. 近期已交付能力（2026-07 窗口，便于不重复造轮）

| 主题 | 提交线索 | 要点 |
|---|---|---|
| 接口 MITM 抓包 | `fccaa62` 等 | 系统代理 loopback、列表真正进流量 |
| Fiddler 列表/滚动 | `d6fc79d` `6cb3bab` | 列、横向滚动 |
| 请求测试+导出导入+Key | `b9592b1` | `iface_request_test`、送入加解密带 Key、停止保留会话 |
| PengToolsHub 命名/文件库/日报/关于 | `a050abc` | 名称、文件库图标与拖拽、日报草稿、关于 Lihp |
| 版本精简/环境请求测试/待升级 | `39b2a65` | `V4 Private`、环境 Base 发送、URL Stretch、今日高亮 |

**当前 HEAD 以 `git log -1` 为准。**

---

## 12. 已知限制 / 待用户现场验证

1. 真实内网 SVN 账号/路径：本开发环境不可验，只能 UI 与单元测试。  
2. 抓包：需用户重启浏览器使系统代理生效；HTTPS 依赖本机信任 mitm CA。  
3. 请求测试发往用户配置的环境：内网服务可用性由用户保证。  
4. `build_private_release.ps1` 内 README 日期替换有一处 `re.error: bad escape \u` 历史告警，一般不阻断 EXE 产出；若脚本大改需单独修。  
5. EXE 正在运行时打包会 `PermissionError`：先结束进程。

---

## 13. 快速「改需求」落点表

| 用户说法 | 优先打开 |
|---|---|
| 待升级列表不对 | `panels/dashboard_panel.py` `_fill_release` |
| 需求树/文件库/SVN | `panels/requirement_panel.py`、`tools/svn_workspace.py` |
| 发版 Excel 列/SQL | `tools/release_prep.py`、`panels/sql_panel.py` |
| 日报草稿/复制今日 | `panels/personal_panel.py` DailyReportTab |
| 加解密/Key | `tools/gateway_crypto.py`、`panels/gateway_panel.py` |
| 抓包没流量 | `tools/http_capture.py`、`tools/ie_proxy.py`、面板信号 ingest |
| 请求测试/环境 | `tools/iface_request_test.py`、面板「请求测试」Tab |
| 主题/按钮看不清 | `ui/theme_manager.py`、`ui/design_system.py`、`resources/style.qss` |
| 导航/版本/关于 | `main_window.py`、`config.py`、`ui/navigation_model.py` |
| 数据路径 | `config.local_data_dir` |

---

## 14. 推荐阅读顺序（新 Grok）

1. **本文**  
2. **`AGENTS.md`**  
3. 改动相关模块源码 + 对应 `tests/test_*.py`  
4. 需要设计背景时再读 `docs/架构/*`、`docs/ui/*`（**以当前代码为准**，旧需求可能过时）  
5. 基线历史：`docs/项目交接/PengToolsV4项目交接文档_V4.26_Private.md`

---

## 15. 一句话交接

**PengToolsHub 是离线 Windows 工具台；数据永远在 EXE 旁 `data/`；Private 抓包只 loopback、报文只内存；请求测试走用户保存环境；停止不清会话；版本显示 `V4 Private`；日常只打私人包并推 `main`。读完 `AGENTS.md` + 本文后，用 CodeGraph 定位，定向测试，再交付。**

---

*文档维护：重大规则变更时同步改 `AGENTS.md` 与本文；实现细节以仓库代码与最新 commit 为准。*
