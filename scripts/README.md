# scripts — 构建与开发工具

| 文件 | 用途 |
|---|---|
| `build_release.ps1` | **唯一发布构建**（原 Private 能力 + 品牌图标） |
| `build_private_release.ps1` | 兼容旧入口，内部转发到 `build_release.ps1` |
| `PengToolsHub.spec` | PyInstaller 生成物（可覆盖） |
| `build_workbook_seed.py` | 开发态：从加密 Excel 生成学习种子 JSON |

## 发布构建

在仓库根目录：

```powershell
.\build_release.ps1
# 或兼容旧命令
.\build_private_release.ps1
```

产物：

- `dist/PengToolsHub.exe`（图标为原 Private 品牌 `pengtools-app-v2.ico`）
- `PengToolsHub_Offline_Setup.zip`
- `Installer/PengToolsHub.exe` + `setup.cmd`

已废弃：

- `PengToolsHub_Private.exe`
- `PengToolsHub_Private_Offline_Setup.zip`
- 旧「标准包 / 私人包」双轨命名（现统一为 PengToolsHub）
