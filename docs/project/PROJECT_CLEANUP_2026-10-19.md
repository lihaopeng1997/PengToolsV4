# 项目清理记录 — 2026-10-19

用户本次明确要求整理本地项目及 Git 中的代码、清理无用文件，并提交代码、编写交接。清理针对当前分支 `dev/2026-09-23`，没有重写 Git 历史或删除远端分支。

## 结果

| 类别 | 实际处理 |
|---|---|
| 历史诊断产物 | 2453 个文件，84,289,914 字节；包含重复 before/after 源码、增量 patch、截图、临时报告，逐文件 SHA256 核验归档后移出项目 |
| 旧构建源码快照 | 3576 个文件，逐文件 SHA256 核验归档后移出项目；保留同目录实际运行包及其 data |
| 可再生成的构建文件 | 删除 41,512 个文件，共 10,624,054,972 字节，约 10.62 GB / 9.89 GiB |
| 旧包 ZIP | 2 个根目录旧 ZIP、12 个历史快照 ZIP 移到项目外的 packages 归档；不进入 Git |
| Git 中的临时脚本 | 删除 `_btn_height_repro.py`、`_btn_height_repro2.py`、`ui-refactor/_check_file_btns.py`；无生产调用方，最后一项还依赖旧源码按钮文本位置 |
| 无用导入 | 清理 Agent、数据中心和 Redis 三个面板的 9 个未使用导入；未改变业务行为 |
| 生成文件防回流 | `.gitignore` 增加 pytest/mypy/ruff/coverage 缓存及生成的 `.spec`；可重用诊断脚本保留在 scripts，产物改为写入 build 或 outputs/_任务目录 |
| 文档入口 | 根 README、项目 README、脚本 README 指向当前交接；修正脚本说明中的旧单文件发布路径及项目入口的过时状态 |

初始 Git 工作区有 2841 个变更/未跟踪文件；其中大部分是诊断副本。有效源码、测试、Vue 源码与嵌入资源、需求/审查记录和精选回执一起提交。旧 bundle 随 Vite 重建替换，不手工编辑。

## 本地归档及恢复

归档根：`D:/PengTools-archive/cleanup-2026-10-19/`，不随 Git 分发。

| 文件 | 内容及校验 |
|---|---|
| `local-diagnostics.zip` | 2453 个历史诊断文件；SHA256 `9f3a17a4b51366a5b2dbaf5016e0b7459030fa2a4448072009d617091087703e` |
| `diagnostics-manifest.json` | 原项目相对路径、长度、逐文件 SHA256；归档条目沿用原相对路径 |
| `historical-build-sources.zip` | 3576 个旧源码/构建说明快照；SHA256 `e7f615f8e301dbb349154afe1a0b357216acb7760635f3e95bd8e24d52b8c5d8` |
| `historical-build-sources-manifest.json` | 对应逐文件路径、长度、SHA256 |
| `packages/` | 14 个移出的旧 ZIP；保留文件，无运行数据迁移 |

需要查某个旧 before 文件或截图时，在归档中按原路径查找。应先解压到临时目录并核对 manifest，按需取证；不要把整包重新覆盖当前源码或用户数据。旧交接中指向这些本地截图、before 目录、patch 的路径应结合本记录定位。它们属于历史证据，不是构建必需输入。

已提交的核心回执仍在 `docs/project/reviews/agent-refactor-2026-10-01/evidence/dc-ssh-mysql-finish-20261014/`：`result.json`、`package-r8.json`、近期定向测试日志等。历史 `source_sha256` 对应当时源码；本次面板的 9 个导入清理会使相应文件哈希变化。

## 清除的主要构建目录

- 无用户数据的 `build_new_20260930_UI4`、`build/local-package-20260920-153536`、`build/PengToolsHub`。
- 无用户数据、且已有 R7/R8 后继包的 `dist/ui-agent-20261014-r2`、`-r4`、`-r5`、`-r6`。
- 其余 11 个历史构建快照中的 `source/build`、`source/Installer`，以及完成归档的重复源码和 ZIP。
- 根目录 `.pytest_cache` 和 `__pycache__`。

所有递归目标先解析绝对路径、限定在 `D:/PengTools`、检查重解析点和应用数据目录；没有使用 `git clean`、reset 或跨 shell 拼接删除。

## 保留项及原因

- `data/`、真实配置、凭据、已有会话和开发环境原样保留。
- R8 当前预览包、带 data 的旧运行包、Installer 发布目录保留；部分历史 `build_new_*/source/dist/PengToolsHub/data` 下有软件数据，因此保留该实际运行目录及原路径。其旁边的重复源码、缓存和 Installer 已清除。
- `.venv-build/`、`frontend/node_modules/` 为当前有效开发依赖；`.workbuddy/`、`.codex_work/` 为工具工作状态，不按垃圾文件删除。
- 已有被文档引用的 Git 截图、有效需求、测试、兼容转发模块保留。模块的“没有直接 import”不能证明无用：导航、Qt 信号、打包和兼容路由存在动态引用。
- 未执行对整个 Git 历史的清除/压缩、远端分支删除或强制推送。当前远端基线 `origin/ui/prism-v1` 已 fetch，仍为 `818fba72bf437de9bd49fcd5b198fb1270096a53`；远端没有同名 `dev/2026-09-23` 分支。
- 进一步批量删除源码子目录缓存的命令被自动审批拒绝，仅返回 `blocked by policy`；该部分缓存保留且已被忽略。

## 验证

本次 20 个 Redis/Mongo 业务与实际离线 SDK 用例通过；另 7 个 Agent 布局、工作区保存恢复和 Redis 原生回调用例通过。Vue typecheck、build:embedded、verify:embedded 通过，重建后的文件名与此前嵌入产物一致。Python 待提交文件 AST 检查通过；提交前另做 staged diff/空白检查。

这不是完整功能、真实数据库、真实内网模型或用户窗口验收。未执行全量测试、Windows 应用启动、完整安装包重建。Redis 值编辑等剩余项见 [详细交接](SESSION_HANDOFF_2026-10-19.md)。
