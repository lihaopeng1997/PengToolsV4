# packaging — 安装与发布布局说明

本目录**不存放**用户数据。实际安装模板在仓库根：

| 目录 | 说明 | Git |
|---|---|---|
| `PrivateInstaller/` | 私人包：`setup.cmd` + `README.txt` + 构建写入的 EXE | 已 gitignore（含 EXE） |
| `Installer/` | 标准包模板 | 已 gitignore |
| 根目录 `*.zip` | 离线安装包产物 | 已 gitignore |

构建入口见 `scripts/README.md`。

规则：

- 安装包**不得**包含用户 `data/`
- 升级只替换 EXE/程序文件，**不得删除**用户 `data/`
- 日常只构建 Private：`.\scripts\build_private_release.ps1`
