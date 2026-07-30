# PengToolsHub UI 改造阶段概览

## 本轮完成
- 全局 UI 第一阶段已复验、提交并推送：四主题 Token 与 QSS 占位符校验、紧凑/舒适密度、启动侧栏偏好、公共 PageChrome、设置页和主窗口接入均保留既有导航与 Stack 副作用。
- 接口排查工作台改造已完成 Task 1 的配置安全基线：新增请求测试纵向分隔条偏好 `request_test_splitter_sizes`，默认 `[360, 640]`。
- 配置归一化改为顶层和 `ui_prefs` 双层白名单；请求/响应正文、Cookie、Token、Authorization 等误传字段不会进入 `data/interface_debug.json`。
- 分隔条偏好只接受两个真实整数，且至少为 `160`/`220`；字符串、浮点、布尔或下限不足会整体回退默认值。

## 关键约束
- 捕获会话和认证信息继续只驻留内存；停止抓包不清空会话，清空仅由用户显式触发或应用退出执行。
- 导航/Stack 映射、SSH per-tab、终端 `TERM_*` 色彩与日志导出规则未改动。

## 验证与产物
- 全局 UI 复验：126/126 通过；接口排查 Task 1 定向回归：40/40 通过。
- 发布前后敏感扫描均通过：HIGH RISK 0、WARN 0。
- 离线发布包已重建：`dist/PengToolsHub.exe`、`Installer/PengToolsHub.exe`、`PengToolsHub_Offline_Setup.zip`；构建信息为 `2026-07-30 08:20:12`。

## 后续
- 按既定计划继续接口排查 Task 2：把开始/停止合并为状态主按钮，并保留测试连接、恢复系统代理和现有会话行为。
