# PengToolsHub

面向研发、交付和运维排查的 Windows 桌面工具台，将需求管理、发版材料、数据库、接口、日志和 AI 辅助集中到一个应用中。

基于 Python 3.12、PyQt6 和 Vue 3，采用单一 `calm` 主题。Vue 提供侧栏与首页，数据库、SQL、SSH 和文件操作等工作台使用原生 PyQt。应用资源随程序分发；数据库、SSH、接口和模型功能按用户配置连接目标服务。

## 主要功能

| 模块 | 用途 |
|---|---|
| 需求与交付 | 需求台账、自定义状态与进度节点、测试点、文件库、SQL 和发版材料整理 |
| 数据中心 | Oracle、MySQL、OceanBase、达梦、Redis、MongoDB 的连接和工作区；关系库 SQL、对象导航、结果查看及 AI 辅助 |
| 接口排查 | 本机流量捕获、请求与响应查看、接口请求测试、接口文档更新 |
| 日志与终端 | SSH 终端、日志截取与导出、AI 日志分析及系统排查 |
| 模型与 Agent | 内网模型聊天、流式输出、项目工作区、受控工具与上下文管理 |
| 开发辅助 | JSON/XML/SQL 格式处理、加解密、命令库、证件与 VIN 数据工具、日报和个人资料 |

各数据库驱动、模型和网络功能需要对应环境配置。功能清单描述源码提供的能力，实际可用范围取决于驱动、权限和目标环境。

## 项目结构

```text
PengToolsV4/
├── run.py                 # 应用入口
├── main_window.py         # 导航、页面装配和生命周期
├── config.py              # 配置定义与用户数据路径
├── panels/                # 页面与工作台
├── tools/                 # 业务逻辑、数据库与 Agent 运行时
├── ui/                    # 公共界面组件和 Qt/Web 适配
├── frontend/              # Vue 3 / TypeScript / Vite 源码
├── resources/             # 图标、模板、技能及嵌入式 Web 资源
├── requirements/          # 构建与开发依赖
├── scripts/               # 环境准备、构建、发布扫描和维护工具
├── packaging/             # 安装脚本与安装说明源文件
├── docs/                  # 架构、设计、需求、使用说明和历史资料
├── requirements.txt       # Python 运行依赖
├── pack.cmd               # Windows 一键打包入口
└── AGENTS.md              # 项目开发约定
```

`data/`、虚拟环境、测试与诊断资料、构建缓存和发布产物保留本地，不纳入 Git。

## 从源码运行

环境要求：Windows、Python 3.12。以下命令在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

需要开发和依赖审计工具时，可使用 `scripts/setup_build_env.ps1 -Development` 创建 `.venv-dev`。数据库客户端、ODBC 驱动和 SVN 等外部工具按所用功能配置，不包含在上述 Python 依赖中。

## 前端开发

前端使用 Node.js 20.19+ 或 22.12+，通过锁文件安装依赖：

```powershell
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build:embedded
npm --prefix frontend run verify:embedded
```

正式应用加载 `resources/webui/vue/` 中的离线资源。修改前端后，从 `frontend/` 重新生成并一并提交嵌入资源；不要手工修改生成的 JS/CSS。

## 构建与安装

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1
.\pack.cmd
# 或直接执行：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

唯一发布配置位于 `scripts/build_release.ps1`，使用独立 `.venv-build` 环境。构建前会扫描发布资源中的敏感信息。

产物为 `dist/PengToolsHub/` 和 `PengToolsHub_Offline_Setup.zip`。安装包包含 `setup.cmd`、安装说明及 `PengToolsHub/` 程序目录；程序依赖同目录 `_internal/`，不能只复制 EXE。安装布局见 [packaging/README.md](packaging/README.md)。

## 数据与配置

开发态数据位于仓库根目录 `data/`；打包后位于 EXE 旁的 `data/`。连接配置、模型设置、需求台账和会话等在本机保存，不应提交到 Git 或放入 `resources/`。

升级前备份数据，替换程序文件时保留 `data/`。不要将真实账号、密码、Token、私钥或捕获报文放入源码、文档和安装包。详见 [安全说明](docs/SECURITY.md)。

## 文档

- [文档导航](docs/README.md)：按架构、设计、功能需求和使用说明查阅。
- [项目开发入口](docs/project/README.md)：源码地图、当前约定和协作方式。
- [使用说明](docs/guides/PengToolsHub_使用说明_V4.27.docx)：桌面软件的用户手册。
- [Prism UI 设计](docs/design/prism/README.md)：现用 V2.1 规格与离线原型。
- [脚本说明](scripts/README.md)：环境、构建和维护命令。

历史设计与交接统一存放在 `docs/archive/`，用于追溯，不代表当前版本已实现或已验收。
