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
├── requirements.txt
├── AGENTS.md              # AI/开发硬规则
└── build_release.ps1      # 便捷入口 → scripts/build_release.ps1
```

依赖方向（强制）：

```
run → main_window → panels → tools / ui / config
ui 不得 import panels / tools
tools 不得 import panels
```

## 快速开始

```powershell
python -m pip install -r requirements.txt
python run.py
```

定向测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_core -v
```

发布打包：

```powershell
.\build_release.ps1
# 产物：dist\PengToolsHub.exe 、 PengToolsHub_Offline_Setup.zip
```

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
- 唯一发布包：`PengToolsHub_Offline_Setup.zip` / `PengToolsHub.exe`（原 Private 能力 + 品牌图标）。

## 安全与分发

- **禁止**把真实账密 / VPN / Token 写入 `resources/`（会打进 EXE）。
- 打包前自动扫描：`python scripts/scan_release_secrets.py`（`build_release.ps1` 已集成，失败则中止）。
- 内置学习种子仅为安全空模板；私有笔记只存本机 `data/`。
- 详见 [docs/SECURITY.md](docs/SECURITY.md)。
