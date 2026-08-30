# PengToolsHub

Windows 离线桌面工具台（Python 3.12 + PyQt6）。界面显示名 **PengToolsHub**，版本文案 **V4 Private**。

## 仓库结构（与架构分层一致）

```
PengToolsV4/
├── run.py                 # 入口：QApplication / 主题 / 单实例
├── main_window.py         # 装配：导航、Stack、跨模块信号、托盘
├── config.py              # 配置：local_data_dir、JSON 路径与默认值
├── panels/                # 界面层（12 个业务面板）
├── tools/                 # 无界面业务逻辑层（可单测）
├── ui/                    # 基础 UI 能力层（主题/图标/弹窗/响应式）
├── resources/             # 打进安装包的资源（QSS/图标/模板/种子）
├── data/                  # 开发态用户数据（gitignore，升级保留）
├── tests/                 # 定向单元 / 面板烟雾测试
├── scripts/               # 构建脚本与开发工具
├── docs/                  # 架构 / 交接 / UI 需求文档
├── packaging/             # 安装布局说明
├── Installer/             # 安装模板（gitignore 含 EXE）
├── frontend/              # Vue 3 + TypeScript + Vite 前端迁移骨架（node_modules/ dist/ 不提交）
├── requirements.txt
├── AGENTS.md              # AI/开发硬规则
└── build_release.ps1      # 便捷入口 → scripts/build_release.ps1
```

依赖方向（强制）：

```
run → main_window → panels → tools / ui / config
tools 不得 import panels / ui
ui 不得 import panels；公共 UI 组件仅可调用无 QWidget 的窄接口工具
```

## 快速开始

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1 -Development
.\.venv-dev\Scripts\python.exe run.py
```

发布构建使用独立的 Python 3.12 环境，避免与本机其它项目的依赖冲突：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

定向测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv-dev\Scripts\python.exe -m pytest -q tests\test_core.py
```

全量测试按文件隔离 PyQt6 全局状态：

```powershell
.\.venv-dev\Scripts\python.exe scripts\run_test_suite.py
```

依赖漏洞审计：

```powershell
.\.venv-dev\Scripts\python.exe -X utf8 -m pip_audit -r requirements.txt
```

Frontend（Vue 3 + TypeScript + Vite，迁移骨架，Node ≥ 20.19 或 ≥ 22.12）：

```powershell
cd frontend
npm ci         # 按 package-lock.json 精确还原依赖
npm run check  # vue-tsc 类型检查 + Vite 双入口构建（file:// 相对产物）+ verify:dist 守护
```

`frontend/dist/` 为开发构建产物（gitignore）；`npm run build:embedded` 产出正式运行时资源 `resources/webui/vue/`（已提交，随 PyInstaller 整体打包）。STEP-4 起左侧 Sidebar 由 Vue 渲染（`vue/chrome.html`），Dashboard 仍为 legacy `dashboard.html`。

发布打包：

```powershell
.\build_release.ps1
# 产物：dist\PengToolsHub\PengToolsHub.exe 、 PengToolsHub_Offline_Setup.zip
```

发布产物为 onedir 目录（PyInstaller onedir）：

```
dist
└── PengToolsHub
    ├── PengToolsHub.exe
    └── _internal\
```

离线包 `PengToolsHub_Offline_Setup.zip` 解压后：

```
setup.cmd
README.txt
PengToolsHub
├── PengToolsHub.exe
└── _internal\
```

日常使用双击 `PengToolsHub\PengToolsHub.exe`；升级时替换 PengToolsHub 目录内的程序文件，必须保留 `PengToolsHub\data`（用户数据目录，首次运行后产生）。

## 文档入口

| 文档 | 说明 |
|---|---|
| [AGENTS.md](AGENTS.md) | 硬规则（边界/导航/数据） |
| [Grok 完整交接](docs/项目交接/PengToolsV4_Grok接手完整交接文档_V4.27_Private.md) | 接手开发必读 |
| [整体架构](docs/架构/PengToolsV4_项目整体架构文档_V1.0.md) | 分层与规范 |
| [docs/README.md](docs/README.md) | 文档目录索引 |

## 产品边界（摘要）

- 离线优先；用户数据只在 `config.local_data_dir()`（开发 `./data/`，打包 `<exe旁>/data/`）。
- Private 抓包仅 loopback；报文只存内存。
- 唯一发布包：`PengToolsHub_Offline_Setup.zip`（内含 `setup.cmd`、`README.txt` 与 `PengToolsHub\` 程序目录，程序入口 `PengToolsHub\PengToolsHub.exe`）。

## 安全与分发

- **禁止**把真实账密 / VPN / Token 写入 `resources/`（会打进 EXE）。
- 打包前自动扫描：`python scripts/scan_release_secrets.py`（`build_release.ps1` 已集成，失败则中止）。
- 内置学习种子仅为安全空模板；私有笔记只存本机 `data/`。
- 详见 [docs/SECURITY.md](docs/SECURITY.md)。
