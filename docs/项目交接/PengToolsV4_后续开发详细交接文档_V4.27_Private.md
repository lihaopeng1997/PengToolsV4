# PengToolsV4 Private 后续开发详细交接文档（归档索引）

> **已由更新文档取代**：请直接阅读  
> **`docs/项目交接/PengToolsV4_Grok接手完整交接文档_V4.27_Private.md`**  
> 以及根目录 **`AGENTS.md`**。  
> 下文保留作历史索引，细节可能滞后于代码。

更新时间：2026-07-21  
仓库：`https://github.com/lihaopeng1997/PengToolsV4.git`  
工作目录：`D:\development\workspace\Codex\自研软件\PengTools\PengToolsV4`

## 1. 交接目的与产品定位

PengToolsV4 是 Python 3.12 + PyQt6 的 Windows 离线桌面效率工具，服务于需求/BUG、SVN 文件、SQL 发版、日报、个人学习、接口排查、加解密和开发格式化等日常工作。

产品原则：少点击、本地优先、数据可升级、Private 与标准版隔离、敏感报文只在内存、企业级且安静易读。

接手人必须先阅读：

1. 根目录 `AGENTS.md`；
2. **`docs/项目交接/PengToolsV4_Grok接手完整交接文档_V4.27_Private.md`（当前主交接）**；
3. `docs/项目交接/PengToolsV4项目交接文档_V4.26_Private.md`（基线历史）；
4. 最新 `.codex_work/grok_handoff/*.md`（若存在）。

## 2. Codex、Grok 与后续老师的职责

### Codex（项目经理/验收）

- 将用户口头诉求整理成可开发 Markdown 文档；
- 结合代码确定落点、数据兼容、安全边界和验收标准；
- 给 Grok 提供可直接粘贴的开发指令；
- 阅读 Grok handoff，核对需求覆盖、测试、构建和 Git；
- 只做本轮改造模块的定向验收，不强行全量回归；
- 明确区分自动化测试、本地测试和用户内网待验证项；
- 防止标准安装包、用户 data 和 Private 安全边界被破坏。

### Grok（实现者）

- 阅读需求文档和 `AGENTS.md`；
- 使用 CodeGraph 定位调用链；
- 实现代码、定向测试和 Private 打包；
- 不创建第二套业务逻辑；
- 写 `.codex_work/grok_handoff/日期-主题.md`；
- 用户未要求暂停时，提交并推送 `origin/main`；
- 如实记录限制，不能将内网抓包说成已验证。

### 后续老师（接手人）

可同时承担产品、开发、验收，但必须遵守：先文档、后代码；只改明确范围；不覆盖未提交工作；不删除 data；不改标准包；不把敏感报文写日志或磁盘。

## 3. 当前功能架构

| 模块 | 主要代码 | 职责 |
|---|---|---|
| 工作台 | `panels/dashboard_panel.py` | 待升级事项、最近需求、快捷入口 |
| 证件/VIN | `panels/credit_panel.py`、`panels/vin_panel.py` | 证件与 VIN 辅助 |
| 发版联动 | `panels/sql_panel.py`、`tools/release_prep.py` | SQL、系统、需求/BUG、发版 Excel |
| 接口文档 | `panels/docx_panel.py`、`tools/docx_updater.py` | DOCX/接口文档处理 |
| 网关解密 | `panels/gateway_panel.py`、`tools/gateway_crypto.py` | 报文加解密、JSON/XML |
| 运维 | `panels/ops_panel.py`、`tools/ops_commands.py` | 命令查询与复制 |
| 设置 | `panels/settings_panel.py`、`config.py` | 主题、字体、语言、悬浮栏、关闭行为 |
| 自我学习/日报 | `panels/personal_panel.py`、`tools/personal_knowledge.py`、`tools/daily_reports.py` | 本地资料与日报 |
| 需求管理/文件库 | `panels/requirement_panel.py`、`tools/requirements.py`、`tools/svn_workspace.py` | 树、文件库、SVN、SQL 联动 |
| 格式工具 | `panels/format_panel.py`、`tools/xml_formatter.py`、`tools/text_dev_helpers.py` | JSON/XML/SQL/Base64/URL/Unicode/时间戳/Java 堆栈 |
| 接口排查 | `panels/interface_debug_panel.py`、`tools/browser_debug.py`、`tools/ie_proxy.py` | 本机代理、CDP、IE、Fiddler 式工作台 |

导航约束：接口排查为 Private 导航 12 / Stack 11，格式工具为导航 11 / Stack 10；新增模块不得随意改变旧映射。

## 4. 已完成的重要能力

- 四套主题（含“夜间安读”）和响应式四档布局；
- `ResponsiveActionBar`、主次按钮收纳、低高度布局；
- Private 自我学习、日报、需求管理和升级准备联动；
- Excel/Word/TXT/SQL/JSON/XML 本地资料管理；
- 文件库：类型、大小、时间、路径、列排序/列宽、展开折叠、拼音搜索；
- SQL 整理展示名已逐步改为“发版联动”，旧 key 保持兼容；
- 多浏览器接口中心：通用代理、Chromium CDP、IE 代理、草稿生成；
- 高对比任务栏图标与单实例方案已进入主线；
- 加解密参数区显示 Key、算法、模式、Padding、编码、IV 说明；
- XML 格式化、开发文本辅助和 Fiddler 式四 Tab 详情。

## 5. 数据、升级与安全

唯一数据根为 `config.local_data_dir()`：开发环境是 `PengToolsV4/data/`，打包后是 EXE 同级 `data/`。升级只替换 EXE/程序资源，不得删除、覆盖、复制或清空 data；安装包不得携带真实用户 data。

配置文件可能包括：

- `settings.json`：主题、语言、字体、悬浮栏；
- `requirements.json`：需求/BUG、系统、日期、SVN、SQL、完成状态；
- `interface_debug.json`：路径、端口、代理/证书指纹和 UI 偏好；严禁报文、Cookie、密钥；
- `requirement_ui.json`：树、splitter、列宽；
- `personal_knowledge.json`、`daily_reports.json`、`system_config.json`。

新增字段必须 `setdefault` 兼容旧数据，写回时保留未知字段。任何删除二次确认，默认焦点为取消。

接口排查只允许 `127.0.0.1`，禁止远程 host、局域网、公网监听；报文、Cookie、Token、Key、明文只存内存，停止监听/切换模式/退出必须清空；Postman/cURL 只生成草稿，不能实际发送请求。IE 代理启动前备份 WinINet，停止/失败/退出恢复；内网抓包必须用户现场验证。

## 6. UI 基线

主题统一走 `ui/theme_manager.py` token 和 `resources/style.qss`。夜间主题禁止白色卡片、白色 Loading、浅色弹窗突兀出现。Excel/Word 原始单元格样式属于业务内容例外。

响应式断点：Wide ≥1440、Standard 1280–1439、Compact 1080–1279、Narrow 960–1079。不得缩小字体或把按钮/树文字截成半截；次要操作进入“更多”，重要节点可换行并通过 Tooltip 展示全文。长任务 Loading 为独立浮层，不推动布局。

## 7. 代码定位与开发流程

```powershell
cd D:\development\workspace\Codex\自研软件\PengTools\PengToolsV4
Get-Content -Raw AGENTS.md
git status --short
git log --oneline -10
codegraph status .
codegraph query -p . -l 20 <symbol>
codegraph callers -p . <symbol>
codegraph callees -p . <symbol>
```

流程：读取规则和最新 handoff → 检查未提交改动 → 写 `docs/ui/` 需求 → 定位调用链 → Grok 实现 → 运行本轮定向测试 → 构建 Private → 启动检查 → 写 handoff → 检查标准包未变 → Git commit/push。

常用命令：

```powershell
python -m pip install -r requirements.txt
python run.py
python -m unittest tests.test_interface_debug tests.test_interface_fiddler_workbench -v
python -m unittest tests.test_filelib_pinyin_release tests.test_startup_and_crypto_params tests.test_release_ui -v
.\build_private_release.ps1
Start-Process .\dist\PengToolsHub_Private.exe
git add <明确文件>
git commit -m "<message>"
git push origin main
```

只运行本轮改造相关测试，不要求全量回归。构建脚本偶尔会有 README `bad escape \\u` 历史警告；只要 EXE/ZIP 成功生成，应如实记录而不是隐瞒。

## 8. 当前最新交付状态

最新 handoff：`.codex_work/grok_handoff/20260721-iface-filelib-pinyin-release.md`。

该轮摘要记录：接口通用代理与 loopback 探测、文件库、拼音搜索、发版联动命名、解密参数均已实现；相关定向测试 **75 项通过**；Private EXE/ZIP 构建成功；标准安装包未修改。

已知限制：

1. 未安装 `pypinyin` 时全拼降级，首字母和原文仍应可用；
2. 真实浏览器/IE 内网抓包、证书和代理仍需用户现场验证；
3. CDP 某些跨域/缓存场景可能拿不到响应体，但应保留请求摘要；
4. 文件库本地删除和 SVN 提交是两个动作；
5. 发版联动下部分旧 Tab 文案可能还需收口；
6. 文件列顺序拖动、复杂 SVN 工具栏收纳等属于后续优化项；
7. 当前工作区可能有未提交变更，接手人不可覆盖他人工作。

## 9. 后续优先级

### P0

- 用户现场验证通用代理是否能在内网 Chrome/Edge/IE 显示真实接口；
- 若无流量，核对代理端口、WinINet、证书、mitmproxy worker、事件回调和过滤条件；
- 验证单实例：双击、托盘隐藏、异常退出、Private/标准同时运行。

### P1

- 完成“发版联动”所有旧“SQL 整理/升级准备”文案收口；
- 在 1440/1280/1080/960 四档实际检查需求树、文件库和接口工作台；
- 安装 `pypinyin` 后验证全拼、首字母、混合搜索；
- 验证文件列表排序、列宽和树节点换行。

### P2

- 修复构建脚本 README `bad escape \\u` 警告；
- 增加真实接口监听探针页和跨域响应体兼容；
- 增加文件列顺序保存；
- 统一所有模块的“更多”按钮和 Tooltip。

## 10. 接手检查清单

- [ ] 已阅读 AGENTS、基线交接文档、本文件和最新 handoff；
- [ ] 已确认 Private/标准版边界和 data 保留规则；
- [ ] 已执行 `git status`，识别未提交改动；
- [ ] 已启动 Private EXE，确认窗口、托盘和数据目录；
- [ ] 已运行当前改造模块定向测试；
- [ ] 已理解接口敏感数据只存内存；
- [ ] 已理解内网 SVN/真实抓包必须用户现场验收；
- [ ] 新需求已写入 Markdown 文档；
- [ ] 交付包含 handoff、Git commit、GitHub push。

## 11. 交接结论

后续重点不是盲目增加模块，而是让接口监听在真实内网可用、文件库和发版联动足够省操作、升级不丢本地数据、主题和缩放始终可读，并持续保持标准安装包不受影响。

统一工作方式：先确认范围 → 查代码落点 → 写需求文档 → Grok 实现 → 定向测试 → Private 构建 → handoff → GitHub 可回滚提交。
