# scripts — 构建与开发工具

| 文件 | 用途 |
|---|---|
| `setup_build_env.ps1` | 创建独立 Python 3.12 构建环境；加 `-Development` 创建测试/审计环境 |
| `build_release.ps1` | **唯一发布构建**（原 Private 能力 + 品牌图标） |
| `build_private_release.ps1` | 兼容旧入口，内部转发到 `build_release.ps1` |
| `run_test_suite.py` | 按测试模块隔离进程；必要时按用例隔离 PyQt6 全局状态 |
| `scan_release_secrets.py` | 发布前扫描会打包的敏感信息 |
| `build_workbook_seed.py` | 开发态：从加密 Excel 生成学习种子 JSON |

`build_release.ps1` 是唯一权威发布配置。PyInstaller 运行时可能生成 `.spec` 文件，但它们属于可再生中间产物，不纳入版本控制，避免形成第二套发布参数。

## 发布构建

在仓库根目录：

```powershell
.\scripts\setup_build_env.ps1
.\build_release.ps1
# 或兼容旧命令
.\build_private_release.ps1
```

开发与依赖审计：

```powershell
.\scripts\setup_build_env.ps1 -Development
.\.venv-dev\Scripts\python.exe scripts\run_test_suite.py
.\.venv-dev\Scripts\python.exe -X utf8 -m pip_audit -r requirements.txt
```

产物：

- `dist/PengToolsHub.exe`（嵌入高对比任务栏图标 `resources\brand\pengtools-taskbar-hc.ico`）
- `PengToolsHub_Offline_Setup.zip`
- `Installer/PengToolsHub.exe` + `setup.cmd`

已废弃：

- `PengToolsHub_Private.exe`
- `PengToolsHub_Private_Offline_Setup.zip`
- 旧「标准包 / 私人包」双轨命名（现统一为 PengToolsHub）
