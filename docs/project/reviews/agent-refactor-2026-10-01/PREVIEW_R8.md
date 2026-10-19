# PengToolsHub R8 预览说明 — 2026-10-15

程序：`D:\PengTools\dist\ui-agent-20261015-r8\PengToolsHub\PengToolsHub.exe`。

运行时保留整个 PengToolsHub 目录及 `_internal`；先通过软件自身正常退出旧预览，再启动 R8。此包未由助手启动，不迁移旧数据、配置或模型密钥。

包含菜单标题/快捷工具跳转修复、结果查找/长值详情、Oracle LOB owner 读取、50/200/500 分页、服务端表筛选排序、SQL 光标/选区定位、执行全部及逐语句结果、完整选中非结果集过程块。

构建和静态包核对通过（61 必需模块、215 个冻结源码代码对象、7 个 Web 资源、Qt/ODBC）。这不等于真实数据库、内网模型或界面验收通过。WITH 本地函数返回查询、输出绑定和 DBMS_OUTPUT 展示仍有限制；达梦延后，整体任务未完成。

SHA256：`d17298669e1de33588499758e1a356e36f37de0aca5d8838a5990a23af358847`。
