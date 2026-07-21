# scripts — 构建与开发工具

| 文件 | 用途 |
|---|---|
| `build_private_release.ps1` | **日常唯一构建入口**（Private 私人包） |
| `build_release.ps1` | 标准包（常规维护禁止） |
| `PengToolsHub_Private.spec` / `PengToolsHub.spec` | PyInstaller 配置（由脚本生成/覆盖亦可） |
| `build_workbook_seed.py` | 开发态：从加密 Excel 生成学习种子 JSON |

## 私人包构建

在**仓库根目录**执行：

```powershell
.\scripts\build_private_release.ps1
```

脚本会将工作目录切到仓库根，使用根下 `run.py` / `resources/` / `PrivateInstaller/`。

产物：

- `dist/PengToolsHub.exe`
- `PengToolsHub_Private_Offline_Setup.zip`（根目录，已 gitignore；安装包内 EXE 名为 PengToolsHub.exe）
