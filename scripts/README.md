# scripts — 构建与开发工具

| 文件 | 用途 |
|---|---|
| `build_release.ps1` | **唯一发布构建**（原 Private 能力 + 品牌图标） |
| `build_private_release.ps1` | 兼容旧入口，内部转发到 `build_release.ps1` |
| `build_workbook_seed.py` | 开发态：从加密 Excel 生成学习种子 JSON |

`build_release.ps1` 是唯一权威发布配置。PyInstaller 运行时可能生成 `.spec` 文件，但它们属于可再生中间产物，不纳入版本控制，避免形成第二套发布参数。

## 发布构建

在仓库根目录：

```powershell
.\build_release.ps1
# 或兼容旧命令
.\build_private_release.ps1
```

产物：

- `dist/PengToolsHub.exe`（嵌入高对比任务栏图标 `resources\brand\pengtools-taskbar-hc.ico`）
- `PengToolsHub_Offline_Setup.zip`
- `Installer/PengToolsHub.exe` + `setup.cmd`

已废弃：

- `PengToolsHub_Private.exe`
- `PengToolsHub_Private_Offline_Setup.zip`
- 旧「标准包 / 私人包」双轨命名（现统一为 PengToolsHub）
