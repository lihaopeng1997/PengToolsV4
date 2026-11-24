<p align="center">
  <img src="resources/brand/pengtools-app-mark.svg" alt="PengToolsHub Logo" width="76" height="76" />
</p>

<h1 align="center">PengToolsHub</h1>

<p align="center">
  <strong>面向研发、交付与运维排查的 Windows 离线桌面工具台 · Prism Calm V4.27.5</strong>
</p>

<p align="center">
  Python 3.12 · PyQt6 · Vue 3 / TypeScript · 单一晴空棱镜主题 · 纯本地离线分发
</p>

---

**PengToolsHub** 将日常研发与交付高频使用的 **数据中心、AI 助手、需求与发版联动、接口抓包、SSH 日志排查、格式工具与日报** 收拢到同一个轻量桌面入口中。

- **混合桌面架构**：Vue 3 承载悬浮侧栏与便当格首页；数据库、SQL、SSH 终端、抓包与本地文件工作台保留原生 PyQt6，兼顾视觉精致度与密集操作响应速度。
- **离线优先与数据安全**：所有静态前端、图标、模板、拼音检索引擎与帮助文档随程序本地打包分发，零 CDN、零账号登录、零云端遥测；用户连接配置与业务数据仅存于本机 `data/`。
- **单一晴空棱镜主题（`calm`）**：全站统一 V5 棱镜折叠品牌图标、悬浮双卡工作区、像素对称标签与紧凑控件体系。

---

## 核心功能模块

| 分组 | 模块 | 核心能力 |
|---|---|---|
| **工作台** | **首页 & 悬浮工具栏** | 便当格工作概览、**「简洁 / 完整」双视图切换**、月度足迹快速记录与单条删除、与**日报实时双向同步**、可定制快捷悬浮球与便携 AI 对话 |
| **数据中心** | **统一数据中心** | 支持 **Oracle、MySQL、OceanBase、达梦、Redis、MongoDB**；提供对象树导航、SQL 安全守卫、结果查看/导出与数据库 AI 辅助 |
| **智能助手** | **模型聊天 / Agent 工作台** | 内网模型流式对话、会话隔离、离线检索；项目目录绑定、受控工具执行与上下文管理 |
| **交付管理** | **需求管理 / 发版联动 / 接口文档更新 / 日报** | 需求台账、自定义流转与测试点管理；一键生成规范发版 SQL 与目录并支持提签；SQL 自动更新接口 Word 文档；按月归档富文本插图日报 |
| **运维工作台** | **日志排查 / 命令库** | 多机 SSH 会话终端、独立浅色底部命令坞、按需 AI 只读命令生成、关键字日志截取导出与 AI 日志排查；内置 **119+ 运维与 JVM 诊断命令**，一键带入终端或 AI |
| **开发工具** | **接口排查 / 格式工具 / 测试数据** | 本机 `127.0.0.1` 代理抓包、接口测试与代理自动恢复；JSON / XML / SQL / 文本辅助 + **图片 & PDF ↔ Base64 离线双向互转**；合并式「测试数据」工作台（证件类型 + 车辆 VIN） |
| **效率与引导** | **自我学习 / 新手引导 / 全局检索 / 更新公告** | 本地知识库与笔记；**10 大核心模块交互式聚光灯新手引导**；`Ctrl+K` 高频别名 + 离线拼音/首字母全局跳转；**启动版本更新公告**（支持本次/永久不再提醒） |

> 说明：各数据库驱动、内网模型接口与 SVN 等外部能力需配合本机驱动或目标环境使用；「测试数据」生成的证件号与 VIN 仅用于软件研发测试。

---

## 更新记录与启动更新公告

- 完整版本变更历史见 **[CHANGELOG.md](CHANGELOG.md)**。
- 软件内置启动更新公告数据源位于 [resources/help/release_notes.json](resources/help/release_notes.json)：
  - 每次发版只需在该 JSON 顶部追加新版本条目并更新 `latest_version`，用户升级后首次打开软件即会自动弹出更新摘要；
  - 弹窗支持勾选 **「本次更新不再提醒」**（仅忽略当前版本，未来发新版时自动再次提醒）或 **「永久关闭弹窗」**，任何时候均可通过顶部 **「帮助 → 更新公告」** 重新查看或恢复提醒。

---

## 项目结构

```text
PengToolsV4/
├── run.py                          # 应用启动入口、单实例锁与异常兜底
├── main_window.py                  # 主窗口、导航装配、生命周期与更新公告触发
├── config.py                       # 配置 Schema、默认值与用户数据路径
├── panels/                         # 业务工作台页面（原生 PyQt6）
├── tools/                          # 纯业务逻辑、数据库/SSH/Agent 运行时与更新公告引擎
├── ui/                             # 公共视觉组件、主题系统、聚光灯引导、公告弹窗与 Web 桥接
├── frontend/                       # Vue 3 / TypeScript / Vite 前端源码（侧栏 & 首页）
├── resources/                      # V5 品牌图标、QSS 样式、内置使用说明、更新公告 JSON 与离线前端产物
│   ├── brand/                      # V5 Prism Fold SVG / 9 档多尺寸 ICO / PNG
│   ├── help/                       # 内置使用说明 (user_guide.html) 与更新公告 (release_notes.json)
│   └── webui/vue/                  # 从 frontend/ 构建生成的离线静态资源（勿手改）
├── packaging/                      # 安装脚本 (setup.cmd) 与用户安装说明
├── scripts/                        # 构建环境初始化、一键打包脚本与发布前敏感信息扫描
├── docs/                           # 架构、设计、功能需求、使用说明与历史归档
├── CHANGELOG.md                    # 面向用户的版本更新日志
├── pack.cmd                        # Windows 一键离线打包入口
├── requirements.txt                # Python 运行依赖
└── AGENTS.md                       # 工程架构边界与协作开发规范
```

`data/`、虚拟环境、本地测试/诊断脚本、构建缓存与安装包产物均保留在本地，不纳入 Git。

---

## 从源码运行

环境要求：**Windows 10 / 11**、**Python 3.12**。在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

如需开发与依赖审计环境，可执行 `scripts/setup_build_env.ps1 -Development` 创建 `.venv-dev`。数据库客户端、ODBC 驱动与 SVN 客户端按需自行配置。

---

## 前端开发（Vue 3 + TypeScript）

前端使用 Node.js 20.19+ 或 22.12+：

```powershell
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build:embedded
npm --prefix frontend run verify:embedded
```

修改 `frontend/` 后必须重新生成 `resources/webui/vue/` 并在同一提交中一并提交；禁止手工编辑构建产物或引入外部 CDN。

---

## 一键离线打包与升级

```powershell
# 首次构建前准备独立打包环境（.venv-build）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1

# 执行发布前敏感扫描并生成离线安装包
.\pack.cmd
# 或：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

- 构建产物为 `dist/PengToolsHub/` 与 `PengToolsHub_Offline_Setup.zip`（包含 `setup.cmd`、安装说明及完整 `PengToolsHub/` 目录）。
- 运行依赖同级 `_internal/` 目录，请整包分发，不要只复制单个 EXE。
- 升级覆盖时**请保留原有 `data/` 目录**，升级前建议备份配置与台账。详见 [packaging/README.md](packaging/README.md) 与 [安全与分发说明](docs/SECURITY.md)。

---

## 文档导航

- **[CHANGELOG.md](CHANGELOG.md)**：版本更新日志与发版公告维护说明。
- **[docs/README.md](docs/README.md)**：架构图、源码地图、功能需求与协作入口。
- **[docs/guides/RELEASE_NOTES_V4.27.md](docs/guides/RELEASE_NOTES_V4.27.md)**：V4.27.5 离线分发说明与上线验收清单。
- **[docs/guides/PengToolsHub_使用说明_V4.27.docx](docs/guides/PengToolsHub_使用说明_V4.27.docx)**：桌面软件用户手册（软件内也可在「帮助 → 使用说明」直接查阅最新 HTML 版）。
- **[docs/design/prism/README.md](docs/design/prism/README.md)**：Prism Calm UI 设计规格与离线原型。
- **[docs/SECURITY.md](docs/SECURITY.md)**：安全基线、凭据脱敏与离线分发红线。
- **[scripts/README.md](scripts/README.md)**：构建、环境与维护脚本参考。

历史设计与阶段交接统一归档于 `docs/archive/`，仅作回溯参考。
