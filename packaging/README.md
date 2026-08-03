# packaging — 安装与发布布局说明

本目录**不存放**用户数据。实际安装模板在仓库根：

| 目录 | 说明 | Git |
|---|---|---|
| `Installer/` | 正式安装模板：`setup.cmd` + `README.txt` + `PengToolsHub.exe` | 已 gitignore（含 EXE） |
| `PrivateInstaller/` | 兼容旧路径，构建时同步 EXE | 已 gitignore |
| 根目录 `PengToolsHub_Offline_Setup.zip` | 离线安装包产物 | 已 gitignore |

构建入口见 `scripts/README.md`：`.\scripts\build_release.ps1`。

规则：

- 安装包**不得**包含用户 `data/`
- 升级只替换 EXE/程序文件，**不得删除**用户 `data/`
- EXE 名：`PengToolsHub.exe`；图标：原 Private 品牌 `resources/brand/pengtools-app-v2.ico`
