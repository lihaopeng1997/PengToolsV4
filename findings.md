# 审计发现

## 当前范围

- 用户目标是尽快修复、优化并发布 PengToolsHub。
- 交接文档提供了历史状态和建议顺序，但实际代码与 Git 状态需要重新核对。
- 实际本地仓库位于 `E:\PengTools\PengToolsV4`，用户当前工作区 `E:\PengTools-New` 为空。

## 已确认事实

- 当前本地开发分支已创建为 `dev`，基于 `origin/main` 的 `564fab3c7f61e54e712ff6c018d3137b6ea65877`。
- `main`、`origin/main` 和本地 `dev` 当前指向同一个 `564fab3`。
- 远端 `origin/dev` 仍指向旧提交 `89b221e`，不能作为当前开发基线。
- 当前工作区只有本轮创建的 `task_plan.md`、`findings.md`、`progress.md` 未跟踪文件。
- 仓库存在 `.codegraph`，已完成 `codegraph sync .`。
- 项目规范要求将静态检查、自动化测试、打包结果、实际运行和人工验收分别报告。
- 权威全量测试入口是 `scripts/run_test_suite.py`，发布构建入口是 `scripts/build_release.ps1`，发布前需要 `scripts/scan_release_secrets.py`。
- 仓库拥有覆盖架构边界、启动、单实例、抓包、数据库、UI、前端嵌入和发布安全的测试模块。

## 当前检查结果

- Python `compileall` 对入口、面板、工具、UI 和测试目录通过。
- `git diff --check` 通过。
- 前端 `typecheck`、`build:embedded`、`verify:embedded` 通过，构建生成的嵌入资源未产生工作区差异。
- 发布敏感信息扫描通过，构建环境 `pip check` 通过。
- 关键启动和单实例回归通过，Web 壳、Agent 和模型聊天相关定向回归通过。
- 依赖清单回归失败。`requirements.txt` 已锁定 `PyQt6-Qt6==6.11.2`，`tests/test_dependency_manifest.py` 仍期待 `6.11.1`。
- 旧 `.venv-build` 与仓库要求不一致。实际 PyInstaller 为 `6.11.1`，脚本要求 `6.22.2`。Qt 运行时也未与当前锁定文件一致。
- 当前 `dist/PengToolsHub/PengToolsHub.exe` 和 `Installer/PengToolsHub/PengToolsHub.exe` 时间戳为 2026-08-30，早于当前主线，不能作为当前源码的发布产物。
- `.venv-dev` 尚未创建，无法执行权威隔离全量测试和 `pip-audit`。
- `E:\PengTools\PengToolsV4\data` 存在开发态用户数据目录，构建和验收必须继续隔离真实数据。

## 证据边界

- 上述测试属于本地静态或隔离进程回归，尚未证明真实内网 SVN、数据库、抓包、模型网关、Windows 安装升级和人工视觉验收通过。
- 未执行完整 pytest 全量入口，因为当前系统 Python 没有 pytest，`.venv-dev` 尚未创建。
- 未执行正式 PyInstaller 构建，因为当前 `.venv-build` 不满足脚本要求，贸然构建会混用旧依赖。

## 修复与依赖更新

- 已在 `panels/personal_panel.py` 的工作簿展示路径接入已有 `masked_workbook_rows()`，表格和 Tooltip 现在使用敏感字段遮罩，单元格临时查看仍读取原始值并要求显式操作。
- 已将本地忽略诊断脚本同步到当前密码字段和工作簿编辑对话框契约。
- 已将 `mitmproxy` 升级到 `12.3.1`，`cryptography` 升级到 `50.0.2`，`pymongo` 升级到 `4.18.3`。
- 已同步更新 `tools/http_capture.py` 的版本说明，继续保持 HTTP/2 关闭和 127.0.0.1 抓包边界。
- `.venv-dev` 与 `.venv-build` 已按新锁定文件刷新，两个环境 `pip check` 通过。

## 最新验证

- 本地离线验证脚本共 29 个，全部通过。
- 依赖清单回归通过。
- 前端类型检查、嵌入构建和嵌入资源校验通过。
- 发布敏感信息扫描通过。
- `pip-audit` 从 36 个漏洞降至 4 个，剩余 4 个均为 `tornado 6.5.10`，原因是 `mitmproxy 12.3.1` 的上游版本上限为 `tornado<=6.5.10`，修复版本从 6.6 开始。强行覆盖该上限会破坏依赖一致性，当前保留 `mitmproxy` 上游约束，并记录为残余风险。
- 当前尚未完成正式 PyInstaller 构建和 Windows 可见窗口验收。

## 用户流程调整

- 用户明确要求后续不要每次改动都打包。
- 当前流程调整为先做定向回归、离线验证、静态检查和依赖审计，等待用户确认验证结果后再统一执行 PyInstaller 打包。
- 本次打包已中断。生成的临时 EXE 没有完成 ZIP、产物检查或发布验收，不作为发布候选。
- 构建脚本写入的 `resources/build_info.json` 已恢复到当前 `HEAD` 内容。

## 继续验证

- 抓包引擎在临时数据目录和本机回环端口上完成两轮启动、就绪、停止、同端口重启烟测，均通过且无错误回调。
- Python `compileall` 通过。
- 当前仍未执行完整发布打包，遵循用户要求等待验证确认后统一打包。

## 打包状态整理

- 用户要求后续先验证再统一打包，本次中断的 PyInstaller 目录已移到 `E:\PengTools\dist_partial_build_20261121`，不再留在 `dist` 或 `Installer` 中。
- 备份目录包含 `dist_PengToolsHub` 和 `Installer_PengToolsHub` 两份未验收产物，合计约 459 MB 每份，未作为发布版本使用。
- 真实数据仍保存在 `E:\PengTools\dist_data_backup_20261121\PengToolsHub_data`。

## 2026-11-23 性能专项基线

- 稳定 UI、运行时和嵌入资源修复已提交并推送到远端 `main`，提交 `4cb7a5271cd89e82bea31a67a90201bcf90b5e6c`。
- 当前启动已经有两层反馈：`run.py` 的 `StartupSplash` 和主窗口的 `AuroraProgress`。主窗口交互启动步骤完成后会隐藏工作台浮层，常用面板继续通过 `QTimer` 分阶段预热。
- 当前常规开发启动烟测基线来自上一轮记录：`.venv-dev` 约 4.03 秒，系统解释器约 4.06 秒（`PENGTOOLS_SMOKE_EXIT_MS=2500`）。把所有常用 QWidget 同步构造的试验值约 6.74 秒，不能作为默认方案。
- 当前发布目录体积的主因是 Qt WebEngine 运行时，`Qt6WebEngineCore.dll` 约 194 MB，`opengl32sw.dll` 约 19.7 MB。没有产品决策和运行时验证前不删除这些文件。
- 本次在 `QT_QPA_PLATFORM=offscreen` 下的启动烟测触发 Qt WebEngine GPU 崩溃（退出码 `-1073741819`），该结果只说明离屏环境不适合验证 WebEngine 可见启动，不作为产品启动失败结论。
- 当前 `dist/PengToolsHub` 目录约 438.3 MiB，198 个 DLL 约 321.3 MiB。最大单项仍是 `Qt6WebEngineCore.dll`，其次是 `opengl32sw.dll`。构建脚本已有 PyQt5/PySide/tkinter 排除和 WebEngine debug 资源清理，现阶段没有经过运行时验证就能安全删除的同量级资源。
- 五个低频页面模块已在开发环境独立导入探针中通过，未建立外部连接。
