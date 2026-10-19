# UI6 隔离构建审计

日期：2026-09-30。使用已审阅的 package_after_go.ps1 -Approval GO 单次调用官方 scripts/build_release.ps1，构建目标仅为 D:\PengTools\build_new_20260930_UI6\source。官方构建退出码为 0，结果状态为 BUILT_ISOLATED_UI6。

## 产物

- ZIP：D:\PengTools\build_new_20260930_UI6\PengToolsHub_UI6_20260930.zip
  - SHA-256：da6b27901a84d47035c86f8b8b37be0d2a70b5c75ca15e554d03fb4ca34ada94
- Dist EXE：D:\PengTools\build_new_20260930_UI6\source\dist\PengToolsHub\PengToolsHub.exe
- Installer EXE：D:\PengTools\build_new_20260930_UI6\source\Installer\PengToolsHub\PengToolsHub.exe
  - 两个 EXE 的 SHA-256 均为：4a0dbc1c278503cf3fa9f5b129c2efc0cd0f194d2a00c7ed6548e2d031ead7a9

## 输入与隔离核验

- 输入清单为 D:\PengTools\build_new_20260930_UI6\source_snapshot.json，共 258 个获准源码/资源路径；user_data_copied 为 false。前端嵌入资源使用冻结时的当前文件名。
- 构建后将清单中全部 258 个 SHA-256 与工作区实时文件比较，258/258 匹配；分支为 dev/2026-09-23，HEAD 为 818fba72bf437de9bd49fcd5b198fb1270096a53。
- 隔离副本相对输入快照唯一的变化是 resources/build_info.json，用于构建信息写入；工作区中的 258 个源输入均未变化。
- 受保护的 13 个基线文件构建后 13/13 匹配。旧候选目录、用户数据和 UI5 进程未被操作。
- ZIP 共 2,068 个条目；路径段为 data 的条目 0，路径段为 cache、caches 或 __pycache__ 的条目 0。隔离源码目录中没有 data 目录。

## 构建门禁与边界

单次官方构建输出显示依赖检查为 “No broken requirements found.”；配置的发布秘密扫描仅检查 resources 与 packaging，结果为 HIGH RISK 0、WARN 0、PASS。没有运行更广范围的源码或归档秘密扫描；ZIP 核验仅计数数据和缓存路径。允许查看的门禁输出摘录保存在 TEMP：C:\Users\Lenovo\AppData\Local\Temp\pengtools-ui6-prep-20260930-7e2888ca0a254b60a4ccd3e5cd66b862\ui6-build-gates-observed.txt。PowerShell transcript 本身没有记录原生构建输出。

PyInstaller 构建成功。单次 stdout 有因 pyodbc 不是包而跳过 collect_data_files/collect_dynamic_libs 的提示，也有 NDIS.SYS 和若干 Qt Quick/QML 插件 DLL 未解析警告；官方构建仍完成，这些警告不代表相关组件通过运行验证。标准 PyInstaller 分析警告报告（164 行）位于 D:\PengTools\build_new_20260930_UI6\source\build\PengToolsHub\warn-PengToolsHub.txt。此构建结果不证明相关平台或 Qt 插件路径已通过运行验证。构建器报告 PID 9548 不指向现有构建目标后继续锁检查；本次没有启动、结束或操作该进程。

本次没有启动 UI6，也没有做产品运行时验收。需要将独立的界面启动/交互结果与本构建审计分开记录。