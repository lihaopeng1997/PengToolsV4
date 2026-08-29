# packaging — 安装与发布布局说明

本目录**不存放**用户数据。实际安装模板在仓库根：

| 目录 | 说明 | Git |
|---|---|---|
| `packaging/setup.cmd`、`README.txt` | 安装脚本源文件（纳入 Git） | 跟踪 |
| `Installer/` | 构建时复制脚本 + 写入程序目录 `PengToolsHub\` | 已 gitignore |
| `PrivateInstaller/` | 兼容旧路径，构建时同步程序目录 | 已 gitignore |
| 根目录 `PengToolsHub_Offline_Setup.zip` | 离线安装包产物 | 已 gitignore |

构建入口见 `scripts/README.md`：`.\scripts\build_release.ps1`。

发布布局（PyInstaller onedir）：

- `dist\PengToolsHub\`：程序目录（`PengToolsHub.exe` + `_internal\` 运行时）
- `Installer\PengToolsHub\`：程序目录副本，随 ZIP 分发

规则：

- 安装包**不得**包含用户 `data/`
- 升级只替换 `PengToolsHub\` 目录内的程序文件（EXE 与 `_internal\`），**不得删除**用户 `PengToolsHub\data\`
- 程序入口：`PengToolsHub\PengToolsHub.exe`；图标：新六边形品牌 `resources/brand/pengtools-taskbar-hc.ico`
