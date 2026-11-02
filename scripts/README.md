# 构建与维护脚本

所有命令从仓库根目录执行。发布只有一套配置：`scripts/build_release.ps1`。

| 文件 | 用途 |
|---|---|
| `setup_build_env.ps1` | 按 `requirements/build.txt` 创建 `.venv-build`；`-Development` 按 `requirements/development.txt` 创建 `.venv-dev` |
| `build_release.ps1` | PyInstaller 发布构建，执行发布资源扫描并生成安装包 |
| `pack.ps1` | 根目录 `pack.cmd` 的入口，调用发布构建并检查本次产物 |
| `scan_release_secrets.py` | 发布资源敏感信息扫描，构建时自动调用 |
| `maintenance/build_workbook_seed.py` | 按显式输入生成学习种子，输出须遵守资源分发规则 |
| `maintenance/gen_user_guide_docx.py` | 生成 `docs/guides/` 中的使用手册 |

## 发布

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1
.\pack.cmd
# 直接运行权威脚本：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

一键入口日志位于 `build/pack-last.log`。发布产物为 `dist/PengToolsHub/`、`Installer/` 和根目录 `PengToolsHub_Offline_Setup.zip`。虚拟环境、构建缓存、安装目录和 ZIP 不纳入 Git。

## 开发环境与依赖审计

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_build_env.ps1 -Development
.\.venv-dev\Scripts\python.exe -m pip_audit -r requirements.txt
```

历史兼容构建入口、测试运行器和诊断脚本仅保留本地。当前克隆使用以上命令，不依赖旧入口或本机测试目录。
