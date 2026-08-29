# PengToolsHub V4.27 Private — 项目执行规范

本文件是当前仓库的权威协作规则。目标是守住数据与安全边界，同时允许对架构、依赖和工程流程进行有证据的调整；历史文档与本文件冲突时，以当前源码、测试和本文件为准。

## 1. 开工与证据顺序

1. 先读本文件；若存在 `.workbuddy/memory/project_memory.json`，再完整读取它。
2. 网络可达时读取服务器 `/data/codex_memory/PengTools/SHARED.md`；只有接手、大改或事实不清时才读长交接文档。
3. 开工先看 `git status --short --branch`。怀疑 Git 元数据异常时，补做 `git fsck --full`、远端分歧和逐文件内容比对；不得只凭目录名认定某个恢复副本更“新”。
4. 跨模块调用链、影响面或重构时使用 CodeGraph；路径明确的小改直接读源码。用过图谱后执行 `codegraph sync .`。
5. 把静态检查、自动化测试、打包结果、实际运行和人工验收分别陈述，不能用前一项替代后一项。

按需文档：

- 完整交接：`docs/项目交接/PengToolsV4_Grok接手完整交接文档_V4.27_Private.md`
- 历史基线：`docs/项目交接/PengToolsV4项目交接文档_V4.26_Private.md`
- 当前构建信息：`resources/build_info.json`

## 2. 产品与安全边界

- 产品是 Windows 离线桌面工具台，技术基线为 Python 3.12 + PyQt6；无账号、云同步、插件市场、在线更新和遥测。
- 运行代码不得新增公网服务依赖、在线 CDN 或内嵌浏览器内核。允许的网络能力仅包括：
  - 接口排查：只监听 `127.0.0.1` 的 Chromium CDP 和 mitmproxy；不得暴露到局域网，探测请求也只能命中 loopback。
  - 内网模型：默认关闭，仅在用户启用后访问 `data/ai_local.json` 中配置的 loopback/RFC1918 地址；继续执行域名拒绝、DNS rebinding 防护、绕过系统代理和 DPAPI Token 存储。
  - 用户主动配置并确认的 SSH、数据库和接口请求；只读查询仍需用户点击执行。
- 抓包请求、响应、Cookie、Token、密钥和解密明文只存内存，不写日志或 JSON。停止抓包保留内存会话；只有清空和退出可调用 `clear_session()`。
- IE 代理启动前备份 WinINet；停止、失败和退出均恢复；证书只删除配置中记录的指纹。
- Postman/cURL、SQL 和命令草稿只生成，不自动执行；运维助手禁止自动执行破坏性命令。
- 禁止把真实账密、VPN、Token、私钥或用户数据写入 `resources/`。发布前 `scripts/scan_release_secrets.py` 必须通过。

这些边界可以在有明确需求和安全设计时演进，但不能为了省事静默绕过；调整时必须同时更新测试、文档和验收证据。

## 3. 数据与升级

- 唯一用户数据根为 `config.local_data_dir()`：开发态 `data/`，打包态 `<exe 旁>/data/`；不得写入 `_MEIPASS` 或用户主目录。
- 升级只替换程序文件，不删除或覆盖安装目录的 `data`；安装包不得包含用户 `data`。
- JSON 读取使用默认值兼容旧版本，写回保留已知旧字段。任何数据迁移都要有回退或备份方案。
- 删除类操作：取消在左、确认在右，默认焦点为取消。

## 4. 架构边界

主依赖方向：

```text
run → main_window → panels → tools | ui | config
```

- `run.py`：入口、QApplication、高 DPI、QSS 和单实例。
- `main_window.py`：导航、Stack、跨模块信号、托盘与关闭流程。
- `panels/`：业务页面和交互编排。
- `tools/`：无 QWidget 的业务逻辑、协议适配和可测试能力。
- `ui/`：可复用视觉组件和通用交互。
- `config.py`：路径、默认值和配置定位。

硬约束由 `tests/test_architecture_boundaries.py` 守护：

- `tools` 不得 import `ui` 或 `panels`。
- `ui` 不得 import `panels`。
- `ui → tools` 不再一刀切禁止，但只能调用无 QWidget、无业务页面编排的窄接口，并加入测试白名单；数据库枚举等共享契约优先拆到独立小模块。
- 不为消除一个 import 做大范围搬家。超大面板按可验证的业务切面渐进拆分，禁止无测试的“架构重写”。

## 5. 导航与 Stack

| 导航 | 模块 | Stack | 可见性 |
|---:|---|---:|---|
| 0 | 工作台 | 0 | 常显 |
| 1 | 证件类型 | 1 | 常显 |
| 2 | 升级准备（SQL） | 2 | 常显 |
| 3 | 接口文档更新 | 3 | 常显 |
| 4 | VIN | 4 | 常显 |
| 5 | 网关解密 | 5 | 常显 |
| 6 | 运维助手 | 6 | 常显 |
| 7 | 设置 | 7 | 左下角常显 |
| 8 | 自我学习 | 8 | 彩蛋解锁后显示 |
| 9 | 日报 | 8 | 常显 |
| 10 | 需求管理 | 9 | 常显 |
| 11 | 格式工具 | 10 | 常显 |
| 12 | 接口排查 | 11 | 常显 |
| 13 | 日志排查 | 12 | 常显 |
| 14 | SQL 控制台 | 13 | 常显 |
| 15 | 模型对话 | 14 | 常显 |

修改导航必须同步菜单、Stack、状态栏、中英文文案和测试。只有“自我学习”允许隐藏；密钥与解锁入口不写入普通 UI 文案。

## 6. 关键业务联动

1. 需求 → 升级准备 → 发版 Excel：确认升级日期，按系统生成 SQL，写入 `resources/release_workbook_template.xlsx`；23 列 `RELEASE_HEADERS` 必须一致。开发分支 SVN 可空，验证 SQL 不进入 SVN 提交目录。
2. 需求 → 日报：`daily_template()` 只生成草稿，不覆盖用户已写内容。
3. 需求 → SQL 整理 / DOCX：保持主窗口信号 `_receive_requirement_sql`、`_receive_requirement_docx` 的契约。

## 7. UI 约束

- 统一样式使用 `resources/style.qss`；QComboBox/QDateEdit 复用下拉箭头样式。
- 页面遵循 `docs/方案/页面骨架与控件分层规范v1.html` 的 L1 页头、L2 工具栏、L3 筛选条、L4 内容区；`tests/test_page_skeleton.py` 的棘轮基线只降不升。
- Loading 使用不占布局的浮层；静默后台任务 `show_loading=False`；成功、失败、异常都必须结束 Loading。
- 主操作唯一且位于页头右上；筛选条不混入改数据按钮；空状态包含说明与下一步动作。

## 8. Python 环境、依赖与测试

共享 Python 只作为创建虚拟环境的基座，不作为发布判据，也不得为本项目强行升降级其全局包。

```powershell
# 运行、测试与依赖审计
.\scripts\setup_build_env.ps1 -Development
.\.venv-dev\Scripts\python.exe scripts\run_test_suite.py
.\.venv-dev\Scripts\python.exe -X utf8 -m pip_audit -r requirements.txt

# 发布构建
.\scripts\setup_build_env.ps1
.\scripts\build_release.ps1
```

- `requirements.txt` 是运行时锁定；`requirements-build.txt` 增加 PyInstaller；`requirements-dev.txt` 增加 pytest 和 pip-audit。
- 构建脚本只能使用 `PENGTOOLS_BUILD_PYTHON` 或 `.venv-build\Scripts\python.exe`，并校验 Python 3.12、PyInstaller 版本和 `pip check`。
- mitmproxy 的传递依赖存在上游版本上限时，不得通过无约束强制升级制造“表面无漏洞、实际不兼容”的环境；先确认可达性、加运行时缓解并在审计报告保留残余风险。
- PyQt6 全局状态会让同进程全量测试不稳定；权威全量入口是 `scripts/run_test_suite.py`。修改模块仍先跑定向测试，再跑该入口。
- 内网 SVN、真实数据库、真实抓包和模型网关在本环境无法验证时，明确标为“待目标环境/人工验证”。

## 9. 修改、发布与 Git 交付

- 只修改当前目标必需内容；不清理用户已有的无关改动，不用 `reset --hard` 或 `clean` 处理工作区。
- 修复缺陷先建立复现检查或测试；每项修改都应能追溯到需求或审计发现。
- 每轮可交付修改必须重新构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

- 统一产品名 `PengToolsHub`；发布物为 `dist\PengToolsHub.exe`、`Installer\` 和 `PengToolsHub_Offline_Setup.zip`。兼容入口只转发到 `scripts/build_release.ps1`。
- 正常交付顺序：`git status` → 定向测试 → 全量隔离测试 → 安全扫描 → 构建 → 检查产物 → 定向暂存 → commit → `git push origin main`。
- 远端固定为 `https://github.com/lihaopeng1997/PengToolsV4.git`，默认分支 `main`。不得提交 `data/`、虚拟环境、EXE/ZIP、日志、临时截图或 CodeGraph 缓存。
- 若本轮只是探索、半成品或用户明确要求不提交，则不推送；否则完成的可交付修改默认提交并推送。

## 10. 快速定位

- 需求树/文件树/SVN UI：`panels/requirement_panel.py`
- 需求模型/搜索/日报模板：`tools/requirements.py`
- SVN 命令：`tools/svn_workspace.py`
- 升级准备：`panels/sql_panel.py`
- 发版 Excel：`tools/release_prep.py`
- 学习库/日报：`panels/personal_panel.py`、`tools/personal_knowledge.py`、`tools/daily_reports.py`
- 接口排查：`panels/interface_debug_panel.py`、`tools/http_capture.py`
- 构建：`scripts/build_release.ps1`
