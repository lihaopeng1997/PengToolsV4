# UI3 隔离候选包审计（2026-09-30）

- 官方 `scripts/build_release.ps1` 退出 0；构建时间 `2026-09-30 16:00:14`。构建日志的依赖检查通过，release secret scan 为 HIGH RISK 0、WARN 0、PASS。
- ZIP：`D:\PengTools\build_new_20260930_UI3\source\PengToolsHub_UI_Fixes_20260930_v2.zip`，203,666,426 bytes，SHA256 `160885A2F6FC66A3289EC78445AF803F72FD8A0FCA4803DCA72DCEDD13DE0D4C`。共 2068 个 entry，`data` 路径 entry 为 0；ZIP 内没有独立 manifest entry。
- EXE：`D:\PengTools\build_new_20260930_UI3\source\dist\PengToolsHub\PengToolsHub.exe`，16,352,948 bytes，SHA256 `BFCD6554FCB662860846F3DB1D5DD5F44268C7018FD81531DAB35C9DB77E1943`。
- Installer 内 EXE：`D:\PengTools\build_new_20260930_UI3\source\Installer\PengToolsHub\PengToolsHub.exe`，16,352,948 bytes；SHA256 与 dist EXE 相同。
- 隔离源清单 `D:\PengTools\build_new_20260930_UI3\source_snapshot.json`：258 files，`user_data_copied=false`，分支 `dev/2026-09-23`、HEAD `818fba72bf437de9bd49fcd5b198fb1270096a53`。构建前 258 项逐文件 SHA256 全匹配；构建后唯一变化是隔离副本 `resources/build_info.json`，官方构建将时间戳写为 `2026-09-30 16:00:14`。仓库根文件哈希保持 `254403BD9FC39440A67B1B75E6001758123255A810848643E1E24A8E3490C63F`。不要声称构建后清单 258 项全哈希匹配。
- UI2 用户测试现场未触碰；UI3 另建隔离目录。UI3 包窗口视觉、收起类别交互和最终退出流程尚未人工验收；只记录源码定向测试、隔离生产 Chrome fixture 和包审计。
