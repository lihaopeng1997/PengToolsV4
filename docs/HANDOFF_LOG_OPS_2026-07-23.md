# PengTools / 日志排查 交接文档

> **日期**：2026-07-23  
> **作者**：Grok（本轮主开发）  
> **产品**：PengToolsV4（PengToolsHub）  
> **仓库根**：`PengToolsV4/`  
> **给谁**：接手的 Agent / 同事  

---

## 0. 30 秒说清在干什么

我们在做 **PengTools「日志排查」模块**的端到端可用性：

- SSH 多标签终端（每标签独立连接）
- 远端目录浏览（文件库风格图标）
- 关键字截取日志
- **单会话导出** / **多机批量导出**
- 安测相关（凭据 DPAPI、HTTPS 校验证书等）此前已落地

**本轮刚完成**：会话导出弹框选目录、远端/导出列表可拖列宽与横向滚动、去掉「统一路径」、终端连删卡顿优化、终端与 sheet 视觉协调。

---

## 1. 做到了什么地方（完成度）

### 1.1 已完成（可验收）

| 能力 | 说明 | 主要路径 |
|------|------|----------|
| 日志排查页面 | 左操作 / 右终端 | `panels/ops_log_panel.py` |
| 多标签独立会话 | 每标签独立 SSH client + 左侧状态 | 同上 `_term_sessions` |
| 管理服务器弹框 | 不在左侧展开 | `ServerManageDialog` |
| 服务只显示名 | 路径在 tooltip | `_refresh_service_combo` |
| 关键字统一 | 逗号分隔，第一个为主 | `tools/ops_ssh.parse_keywords` |
| 目录绑日志 | 导出时目录 → 最新 `.log` | `resolve_remote_log_file` |
| 导出名规则 | `导出根/关键字/IP-服务.log` | `export_keyword_dir` + `local_export_filename` |
| 会话导出 | **每次弹框选文件夹** | `_export_current_session` |
| 批量导出 | 勾选多机多服务并行 | `_start_export` / `extract_logs_parallel` |
| 远端目录图标 | 对齐需求管理文件库 | `QFileIconProvider` + 4 列 |
| 列宽/横向滚动 | 远端目录、导出目标可拖列宽 | Interactive header |
| 终端配色 | TERM_* 控制台岛 token | `ui/theme_manager.py` + QSS |
| 终端删字卡顿 | BS 序列批量处理 | `ui/ssh_terminal.py` |
| 离线打包 | 每轮交付要打 | `scripts/build_release.ps1` |
| 安测基线 | SSL 校验、远程确认、DPAPI | `docs/SECURITY*.md`、`tools/secure_store.py` |
| 格式化折叠/缩略图 | JSON/XML | `tools/code_folding.py`、`ui/code_glance.py` |

### 1.2 已知可改进（未做完 / 可选）

- 导出时「目录取最新 .log」若用户要**指定日期文件**，需在批量导出树里加文件选择（当前会话模式已有日志文件下拉）
- 终端仍是简易 PTY（非完整 xterm）；复杂 ANSI 全屏程序可能乱
- `scripts/_ux_*.py`、`_patch_*.py` 等为历史补丁脚本，可清理但勿依赖
- 根目录 `PengTools/` 与 `PengToolsV4/` 都可能有 `.git` 痕迹；**以 `PengToolsV4` 为权威仓库**

---

## 2. 绝对不能踩的坑

1. **不要把 SSH 密码写进仓库 / 聊天 / SHARED 记忆**  
   - 存 `data/`，DPAPI/`enc:`；禁止新写 `b64:`

2. **不要把日志排查连接做成全局单例**  
   - 必须按 **终端标签** 存 client；新开标签不得 disconnect 其它标签

3. **导出路径经常是目录不是文件**  
   - 必须 `resolve_remote_log_file`（或等价）解析最新日志，不能直接 grep 目录

4. **导出命名约定**（用户明确要求）  
   - 文件夹 = 主关键字  
   - 文件 = `{IP}-{服务名}.log`  
   - 不要再改回 `host_svc_kw_timestamp` 除非用户同意

5. **PengTools 交付必须打包**  
   - 改完可验收功能后跑 `scripts/build_release.ps1`  
   - 产物：`PengToolsHub_Offline_Setup.zip`、`dist/PengToolsHub.exe`

6. **不要用真实姓名做人名测试数据**（用户明确要求过）

7. **CodeGraph / 记忆服务器**  
   - 有条件应回写；密码只在本机 secrets  
   - 图谱**按需**加载，小改不必每次拉全量

8. **QSS 终端色**  
   - 用 `TERM_*` token，不要把终端重新绑回 `CODE_BG`（会与页面糊成一片）

9. **终端退格**  
   - `normalize_terminal_text` **必须保留 `\x08`**，否则「能输入不能删」  
   - 连删走批量 edit block，不要逐字符刷 UI

10. **Git**  
    - 工作区在 `PengToolsV4`；提交前确认不把 `data/` 私钥、`.env` 打进去

---

## 3. 刚接手时注意什么

```text
[ ] 1. 确认工作目录：PengToolsV4
[ ] 2. 读本文件 + docs/SECURITY.md（安测相关）
[ ] 3. （建议）连服务器读 SHARED 记忆（见 §5）
[ ] 4. python run.py 或 dist/PengToolsHub.exe 打开「日志排查」手测
[ ] 5. 小改可直接改；跨模块再考虑 CodeGraph
[ ] 6. 有实质交付 → build_release.ps1 → 再 commit
```

### 建议手测清单

1. 管理服务器：新增/编辑/测试连接（密码保存）  
2. 连接本会话 → 终端输入、**连按 Backspace** 是否流畅  
3. 新开终端标签：旧会话仍连接；左侧状态独立  
4. 远端目录：图标、列宽拖拽、横向滚动  
5. 会话「导出日志」：每次弹框选文件夹  
6. 批量导出：勾选多服务；树可横向看全路径；无「统一路径」字段  
7. 导出结果目录结构：`关键字/IP-服务.log`

---

## 4. 怎么学习当前项目

| 优先级 | 看什么 | 为什么 |
|--------|--------|--------|
| P0 | `main_window.py` 导航 stack | 模块挂载 |
| P0 | `panels/ops_log_panel.py` | 日志排查几乎全集中于此（大文件） |
| P0 | `tools/ops_ssh.py` | SSH、导出、服务路径、安全存储入口 |
| P0 | `ui/ssh_terminal.py` + `tools/ops_ssh_shell.py` | 终端显示与 PTY |
| P1 | `ui/theme_manager.py` + `resources/style.qss` | 主题 token / 终端岛 |
| P1 | `tools/secure_store.py` | 凭据加密 |
| P1 | `panels/requirement_panel.py` 文件库树 | 远端目录图标参考实现 |
| P2 | `docs/SECURITY_TEST_BASELINE.md` | 安测对照 |
| P2 | `scripts/build_release.ps1` | 打包与密钥扫描 |

运行：

```powershell
cd PengToolsV4
python run.py
# 或
.\scripts\build_release.ps1
```

测试示例：

```powershell
python -m unittest tests.test_ops_ssh -q
```

---

## 5. 服务器共享记忆 / CodeGraph（多 Agent）

| 项 | 值 |
|----|-----|
| 主机 | `39.97.57.230` |
| 用户 | `root` |
| 密码 | **仅本机** `~/.grok/secrets/dev-memory-server.env`（禁止写入仓库/聊天） |
| 标准目录 | `/data/dev-memory/projects/PengToolsV4/{codegraph,memory,compaction}/` |
| 兼容 | `/data/codex_memory/` |
| 权威规范 | `/data/dev-memory/AGENT_SHARED_MEMORY_CODEGRAPH_SPEC.md`（本机 `~/.grok/rules/` 有副本） |

**规则摘要**：

- 共识写 `memory/SHARED.md`；流水写 `memory/agents/<agent>/`
- CodeGraph **按需**加载，不必每次改一行都拉
- 有实质进展应回写记忆 / compaction

**对本项目 slug**：`PengTools`（总）+ `PengToolsV4`（子）

---

## 6. 本轮用过 / 应对齐的 Skills

| Skill | 用途 |
|-------|------|
| （全局）context-optimization / compression / filesystem-context | 长会话、大输出落盘 |
| design / claude-design | 用户要求设计 UI 时参考（终端对比色决策） |
| commit / commit-push-pr | 用户要求提交时 |
| check-work / review | 验收 diff 时 |

**不必每次机械加载 skill**；有对应场景再读 `SKILL.md`。

PengTools 项目级规则：

- 交付节奏：**功能可验收 → 必须离线打包**
- 语言：对用户默认简体中文

---

## 7. 你怎么跟下一位同学 / Agent 说（可直接转发）

---

你好，请接手 **PengToolsV4 日志排查（ops log）** 后续。

**仓库**：`PengToolsV4`（以该目录 git 为准）  
**交接文档**：`docs/HANDOFF_LOG_OPS_2026-07-23.md`（必读）  
**主代码**：`panels/ops_log_panel.py` + `tools/ops_ssh.py` + `ui/ssh_terminal.py`

**当前状态**：多标签独立 SSH、导出命名、文件库风格远端目录、会话导出弹框选目录、终端配色/删字优化已落地；请先手测再改。

**硬约束**：

1. 密码不上库；DPAPI/enc 存储  
2. 会话连接 per-tab，别做全局 disconnect  
3. 导出目录要 resolve 到具体 `.log`  
4. 导出名：`关键字文件夹 / IP-服务.log`  
5. 可验收改动后跑 `scripts/build_release.ps1`  
6. 服务器记忆：`39.97.57.230` 上 `/data/dev-memory/projects/PengToolsV4/`（密码只在本机 secrets）

**建议第一步**：打开交接文档 §3 手测清单，用 `python run.py` 点一遍「日志排查」。

**多 Agent 规范**：见本机 `~/.grok/rules/AGENT_SHARED_MEMORY_CODEGRAPH_SPEC.md` 或服务器权威副本。

---

## 8. 关键文件清单（本主题相关）

```text
panels/ops_log_panel.py          # UI 主面板（大）
tools/ops_ssh.py                 # SSH / 导出 / 关键字 / 服务
tools/ops_ssh_shell.py           # PTY + normalize（保留 BS）
tools/ops_cmd_history.py         # 本机命令历史
tools/secure_store.py            # 凭据
ui/ssh_terminal.py               # 终端控件 + 批量退格
ui/theme_manager.py              # TERM_* tokens
resources/style.qss              # 终端岛 / 导出树样式
docs/SECURITY.md
docs/SECURITY_TEST_BASELINE.md
docs/HANDOFF_LOG_OPS_2026-07-23.md  # 本文
scripts/build_release.ps1
tests/test_ops_ssh.py
```

---

## 9. 变更日志（本交接节点）

- 会话导出：每次 `QFileDialog` 选文件夹  
- 删除批量导出「统一路径」字段  
- 远端目录 / 导出目标：Interactive 列宽 + 横向滚动  
- 终端：BS 批量处理防卡顿；`ops-term-shell` 与页面 sheet 协调  
- 导出目标树改为两列：服务器/服务 | 日志路径  

---

*交接结束。有问题先读本文与 SECURITY 文档，再改代码。*
