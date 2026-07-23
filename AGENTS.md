# PengToolsHub V4.27 Private — 项目规则

> **开工读序（省 token）**：本文件（硬规则）→ 服务器 `SHARED.md`（短共识）→ **仅接手/大改时**再打开交接长文。  
> 完整交接（按需）：`docs/项目交接/PengToolsV4_Grok接手完整交接文档_V4.27_Private.md`  
> 基线历史（按需）：`docs/项目交接/PengToolsV4项目交接文档_V4.26_Private.md`  
> 当前构建信息以 `resources/build_info.json` 为准；界面版本文案为 `V4 Private`（见 `config.app_version_text()`）。
>
> **CodeGraph 按需**：仓库可有 `.codegraph/`；服务器基线见 `PengToolsV4/codegraph/latest.tar.gz`。  
> - **需要时再用**（跨模块、陌生调用链、影响面、重构、用户要求查图谱）  
> - **小改/路径已明确**：直接改源码，不必每次 download/sync  
> - 改完且用过图谱时：`codegraph sync .`  
>
> ```powershell
> # 仅在需要定位调用链/影响面时
> codegraph status .
> codegraph sync .
> codegraph query -p . -l 20 <symbol>
> codegraph callers -p . <symbol>
> codegraph impact -p . <symbol>
> ```

## 产品边界

- Windows 离线桌面工具台（Python 3.12 + PyQt6），无账号、云同步、插件市场、在线更新、遥测。
- 运行代码中不引入 HTTP/WebSocket/浏览器内核/在线 CDN。
- **禁止**把真实账密/VPN/Token/私钥写入 `resources/`（会打进 EXE）；私有笔记只存 `data/`。发布前必须 `python scripts/scan_release_secrets.py` 通过（`build_release.ps1` 已集成）。
- **Private 版唯一例外（接口排查 nav 12）**：允许本机 `127.0.0.1` 的 Chromium CDP（`websocket-client`）与 IE MITM 代理（`mitmproxy`，仅监听 loopback）。禁止连接远程 host、禁止把代理暴露到局域网。
- 接口排查抓到的请求/响应/令牌/Cookie/密钥/明文 **只存内存**；禁止写日志与 JSON。停止抓包**保留会话**（可继续导出/请求测试）；仅「清空」按钮与应用退出调用 `clear_session()`。配置仅允许 `data/interface_debug.json`（路径、端口、本地地址、证书指纹、代理恢复快照）。
- 请求测试按用户在 `interface_debug.json` 保存的**环境 Base**（scheme://host:port）替换抓包 URL 的 host 后发送；可新增/编辑/删除环境。导出明细格式 `pengtools_iface_session_v1`（URL + 优先解密后的请求/响应），可再导入/拖入回填。
- IE 代理：启动前备份 WinINet 设置；停止/失败/退出必须恢复；证书仅删除配置中记录的指纹。
- 接口草稿（Postman/cURL）只生成、不发送网络请求。
- 产品统一为 **PengToolsHub**（含原 Private 能力：接口排查等）；发布包 `PengToolsHub_Offline_Setup.zip`，EXE 名 `PengToolsHub.exe`。
- 发布用 `build_release.ps1`（`build_private_release.ps1` 仅作兼容转发）；图标用 `resources/brand/pengtools-app-v2.ico`。
- **交付节奏（强制）**：每完成一轮可交付功能/修复（用户可验收的一次迭代），**必须重新打包离线安装包**，不要只留源码调试态。
  - 命令：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1`
  - 产物：`dist\PengToolsHub.exe`、`Installer\`、`PengToolsHub_Offline_Setup.zip`
  - 打包前确认依赖已写入 `requirements.txt` / 构建 `hidden-import`（如 paramiko）
  - 打包完成后向用户回报产物路径与 `resources/build_info.json` 构建时间

## 目录与职责

| 路径 | 职责 |
|---|---|
| `run.py` | 入口、高 DPI、QSS、主窗口 |
| `main_window.py` | 导航、Stack、联动、托盘/关闭、彩蛋 |
| `config.py` | `local_data_dir()`、系统/设置/需求 UI 等路径与默认值 |
| `panels/` | 各导航模块 UI（界面层） |
| `tools/` | 无界面业务逻辑（可单测） |
| `ui/` | 主题/图标/响应式/弹窗等公共 UI |
| `resources/` | QSS、图标、发版模板、学习种子 |
| `data/` | 开发态用户数据（不得打进安装包） |
| `tests/` | 定向测试 |
| `scripts/` | 构建脚本与开发工具（权威入口） |
| `docs/` | 架构 / 交接 / UI 需求 |
| `packaging/` | 安装布局说明（产物目录见 README） |
| `Installer/` | 安装模板（setup.cmd + README + 构建写入的 EXE） |
| `PrivateInstaller/` | 兼容旧路径（构建会同步 EXE） |

依赖方向：`run → main_window → panels → tools|ui|config`；`ui` 不 import `panels/tools`；`tools` 不 import `panels`。

## 导航与 Stack（必须同步维护）

| 导航 index | 模块 | 显示 |
|---|---|---|
| 0 | 工作台 | 常显 |
| 1 | 证件类型 | 常显 |
| 2 | 升级准备（SQL） | 常显 |
| 3 | 接口文档更新 | 常显 |
| 4 | VIN | 常显 |
| 5 | 网关解密 | 常显 |
| 6 | 运维助手 | 常显 |
| 7 | 设置 | 常显（左下角） |
| 8 | 自我学习（PersonalPanel） | 彩蛋解锁后显示 |
| 9 | 日报（同一 PersonalPanel） | **常显，禁止再隐藏** |
| 10 | 需求管理 | **常显，禁止再隐藏** |
| 11 | 格式工具 | 常显 |
| 12 | 接口排查 | 常显（Private） |

- 导航 8/9 → Stack 8（PersonalPanel）；导航 10 → Stack 9（RequirementPanel）；导航 11 → Stack 10（FormatToolsPanel）；导航 12 → Stack 11（InterfaceDebugPanel）。
- 只有「自我学习」允许彩蛋隐藏；密钥与解锁入口勿擅自改、勿写进普通 UI 文案。

## 数据与升级硬规则

- 唯一数据根：`config.local_data_dir()`  
  - 开发：`PengToolsV4/data/`  
  - 打包：`<exe 旁>/data/`  
  - **禁止**写到 `_MEIPASS` 或用户主目录。
- 升级 = 替换 EXE 等程序文件；**不得删除/覆盖**安装目录 `data`。
- 安装包不得包含用户 `data`（构建脚本会清 `Installer/data`）。
- JSON 字段：读时 `setdefault`/默认值兼容旧数据；写时保留已知旧字段。
- 删除类操作：取消在左、确认删除在右，**默认焦点取消**。

## 关键业务联动

1. **需求 → 升级准备 → 发版 Excel**  
   先确认升级日期 → 推荐/勾选需求 → 多系统分别生成 SQL → 写 `resources/release_workbook_template.xlsx`（23 列表头 `RELEASE_HEADERS` 必须一致）。开发分支 SVN 可为空，分支列可空，不可阻塞生成。验证 SQL 不进 SVN 提交目录。
2. **需求 → 日报**：`daily_template()` 生成草稿，不覆盖用户已写内容。
3. **需求 → SQL 整理 / DOCX**：主窗口信号 `_receive_requirement_sql` / `_receive_requirement_docx`。

## UI / 交互约束

- 样式统一用 `resources/style.qss`；QComboBox/QDateEdit 复用下拉箭头样式。
- Loading 为不占布局浮层；文件树后台刷新等静默任务 `show_loading=False`。
- 成功/失败/异常三条路径都要结束 Loading。
- 网关解密的密钥/明文/报文默认不落盘、不写日志。
- 接口排查的敏感报文同样不落盘、不写日志；草稿导出由用户显式选择路径。
- 运维助手不自动执行破坏性命令。

## 开发与发布

```powershell
python -m pip install -r requirements.txt
python run.py
.\scripts\build_release.ps1
# 兼容：.\build_private_release.ps1 / .\build_release.ps1 均转发到同一脚本
```

- GitHub 回滚规则：当前仓库远端固定为 `origin https://github.com/lihaopeng1997/PengToolsV4.git`，默认工作分支为 `main`。
- 每次完成一轮可交付改动后，默认执行：`git status` → 定向验证 → `git add` → `git commit` → `git push origin main`。
- 如果本轮只是探索、半成品或用户明确要求暂不提交，则不要强推；除此之外，正常开发默认需要推送到 GitHub，保证可回滚。
- 提交时禁止把 `data/`、标准安装包、临时截图、日志等无关内容推上去，优先遵守 `.gitignore`。

- 测试策略：**本次改造模块定向测试优先**，不必强求全量回归；业务规则要有函数级断言（尤其发版 Excel）。
- 内网 SVN 在本环境无法验证：UI/交付说明只能写「待用户内网验证」。
- 改 `main_window` 导航时同步：菜单、Stack、状态栏文案、中英文。

## 快速定位

- 需求树/文件树/SVN UI：`panels/requirement_panel.py`
- 需求模型/搜索/日报模板：`tools/requirements.py`
- SVN 命令封装：`tools/svn_workspace.py`
- 升级准备 UI：`panels/sql_panel.py`
- 发版 Excel：`tools/release_prep.py`
- 学习库/日报：`panels/personal_panel.py` + `tools/personal_knowledge.py` / `daily_reports.py`
